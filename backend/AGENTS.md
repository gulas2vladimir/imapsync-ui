# backend/

FastAPI app + the imapsync subprocess runner.

## Owns

- `app.py` — FastAPI routes, WebSocket fan-out, run registry, multi-account
  orchestration. HTTP + WS endpoints, run lifecycle (`running → completed |
  aborted | failed`).
- `imapsync_runner.py` — imapsync argv builder, stdout line parser, async
  driver. Defines `Account`, `SyncOptions`, `SyncEvent` and the regex set
  used to map imapsync output to typed events.
- `requirements.txt` — Python deps (FastAPI, uvicorn, websockets, pydantic).

## Work Guidance

- The WebSocket contract is `{type, account_id, data}`. New event types must be
  added in `imapsync_runner._parse_line` and rendered in `frontend/app.js`.
- imapsync passwords flow through short-lived `passfile*` files; never extend
  the argv to pass `--password*`. Files are written by `build_command` and
  scrubbed in the `finally` block of `run_sync`.
- The run registry is in-memory (`runs: dict[str, RunState]`). For multi-host
  deployments swap for Redis or a DB; the `RunState` class is the seam.
- All subprocess work goes through `asyncio.create_subprocess_exec`; never
  use sync `subprocess` from the API handlers.
- imapsync is located by `shutil.which("imapsync") or "/usr/local/bin/imapsync"`.
  Override at test time with `IMAPSYNC_BIN=/path/to/binary`.
- The progress parser's regex set is the truth source for what counts as
  "progress". When imapsync changes its stdout, update regexes here, not in
  the frontend.

## Verification

- `pip install -r backend/requirements.txt` into a venv.
- `IMAPSYNC_LOG_DIR=$(pwd)/logs uvicorn app:app --reload --app-dir backend`
  boots cleanly; `curl localhost:8000/api/health` returns `{"status":"ok"}`.
- WebSocket smoke: open a run via POST `/api/sync`, then connect
  `ws://localhost:8000/ws/<run_id>` and assert `line`, `progress`, and
  `finished` events arrive. See README.