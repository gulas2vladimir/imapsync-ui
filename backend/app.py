"""FastAPI app exposing imapsync runner over HTTP + WebSocket.

Endpoints:
  GET  /                      - serves the web UI
  GET  /api/history           - list past sync runs (from logs dir)
  POST /api/sync              - start a multi-account sync, returns run_id
  GET  /api/sync/{run_id}     - status of a run
  POST /api/sync/{run_id}/abort - abort a run
  WS   /ws/{run_id}           - live event stream for a run
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from imapsync_runner import (
    Account,
    SyncOptions,
    abort_sync,
    run_sync,
)

# --- paths ---------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
LOG_DIR = Path(os.environ.get("IMAPSYNC_LOG_DIR", "/var/log/imapsync-ui"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="imapsync UI", version="1.0.0")

# In-memory run registry. For a multi-instance deployment swap for Redis/pg.
runs: dict[str, "RunState"] = {}


class RunState:
    """All state for one multi-account run."""

    def __init__(self, accounts: list[Account], parallel: bool = False) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.accounts = accounts
        self.parallel = parallel
        self.subscribers: list[asyncio.Queue] = []
        self.status = "running"  # running | completed | aborted | failed
        self.tasks: list[asyncio.Task] = []
        self.results: dict[str, int] = {}  # account_id -> exit code
        self.created_at = asyncio.get_event_loop().time()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "parallel": self.parallel,
            "accounts": [
                {
                    "id": a.id,
                    "name": a.name,
                    "host1": a.host1,
                    "user1": a.user1,
                    "host2": a.host2,
                    "user2": a.user2,
                    "exit_code": self.results.get(a.id),
                }
                for a in self.accounts
            ],
        }


# --- request/response models --------------------------------------------


class SyncOptionsIn(BaseModel):
    ssl1: bool = False
    ssl2: bool = False
    tls1: bool = False
    tls2: bool = False
    delete1: bool = False
    delete2: bool = False
    automap: bool = False
    dry: bool = False
    timeout1: float | None = None
    timeout2: float | None = None
    extra: list[str] = Field(default_factory=list)


class AccountIn(BaseModel):
    name: str
    host1: str
    port1: int | None = None
    user1: str
    password1: str
    host2: str
    port2: int | None = None
    user2: str
    password2: str
    options: SyncOptionsIn = Field(default_factory=SyncOptionsIn)


class SyncRequest(BaseModel):
    accounts: list[AccountIn]
    parallel: bool = False


# --- HTTP routes --------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/sync")
async def start_sync(req: SyncRequest) -> JSONResponse:
    if not req.accounts:
        raise HTTPException(400, "at least one account required")

    accounts = []
    for i, a in enumerate(req.accounts):
        accounts.append(
            Account(
                id=f"acc{i}",
                name=a.name or f"account-{i + 1}",
                host1=a.host1,
                port1=a.port1,
                user1=a.user1,
                password1=a.password1,
                host2=a.host2,
                port2=a.port2,
                user2=a.user2,
                password2=a.password2,
                options=SyncOptions(**a.options.model_dump()),
            )
        )

    run = RunState(accounts, parallel=req.parallel)
    runs[run.id] = run
    run.tasks.append(asyncio.create_task(_drive_run(run)))
    return JSONResponse({"run_id": run.id, "snapshot": run.snapshot()})


@app.get("/api/sync/{run_id}")
async def get_sync(run_id: str) -> dict[str, Any]:
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run.snapshot()


@app.get("/api/runs")
async def list_runs() -> list[dict[str, Any]]:
    return [r.snapshot() for r in runs.values()]


@app.post("/api/sync/{run_id}/abort")
async def abort(run_id: str) -> dict[str, Any]:
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    killed = []
    for acc in run.accounts:
        if await abort_sync(acc.id, LOG_DIR):
            killed.append(acc.id)
    run.status = "aborted"
    return {"aborted": killed}


@app.get("/api/sync/{run_id}/log/{account_id}")
async def get_log(run_id: str, account_id: str) -> dict[str, str]:
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    log = LOG_DIR / f"imapsync_{account_id}.log"
    if not log.exists():
        raise HTTPException(404, "log not ready")
    return {"content": log.read_text(errors="replace")[-200_000:]}


# --- websocket ----------------------------------------------------------


@app.websocket("/ws/{run_id}")
async def ws_run(ws: WebSocket, run_id: str) -> None:
    await ws.accept()
    run = runs.get(run_id)
    if not run:
        await ws.send_json({"type": "error", "data": {"message": "unknown run"}})
        await ws.close()
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    run.subscribers.append(queue)

    # Initial snapshot
    await ws.send_json({"type": "snapshot", "data": run.snapshot()})

    async def pump_out() -> None:
        while True:
            evt = await queue.get()
            await ws.send_json(
                {
                    "type": evt.type,
                    "account_id": evt.account_id,
                    "data": evt.data,
                }
            )

    async def pump_in() -> None:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")

    try:
        await asyncio.gather(pump_out(), pump_in())
    except WebSocketDisconnect:
        pass
    finally:
        if queue in run.subscribers:
            run.subscribers.remove(queue)


# --- run driver ---------------------------------------------------------


async def _drive_run(run: RunState) -> None:
    """Spawn per-account imapsync processes. Default: serial. If parallel: asyncio.gather."""
    emit_tasks = [_run_one(run, acc) for acc in run.accounts]

    if run.parallel:
        await asyncio.gather(*emit_tasks, return_exceptions=False)
    else:
        for t in emit_tasks:
            await t

    # Determine final status
    if run.status != "aborted":
        run.status = "completed" if all(rc == 0 for rc in run.results.values()) else "failed"

    # Tell subscribers we're done
    done_evt = {
        "type": "run_finished",
        "data": {"status": run.status, "results": run.results},
    }
    for q in run.subscribers:
        await q.put(_RawEvent(done_evt))


async def _run_one(run: RunState, account: Account) -> None:
    async def emit(evt) -> None:
        for q in list(run.subscribers):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # drop oldest if client is slow; never block the runner

    rc = await run_sync(account, emit, LOG_DIR)
    run.results[account.id] = rc


# tiny shim so we can put already-serialised dicts on the subscriber queue
class _RawEvent:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = payload["type"]
        self.account_id = ""
        self.data = payload["data"]


# --- static UI ----------------------------------------------------------


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_DIR),
    name="static",
)