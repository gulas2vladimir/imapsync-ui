"""imapsync subprocess wrapper with live progress parsing.

imapsync prints human-readable status to stdout. We tail it line-by-line and
extract progress signals we can stream to the browser.

Progress signals we look for:
  - "Host1: nb messages: NNN" / "Host2: nb messages: NNN" (folder sizes start)
  - "Total bytes host1: NNNN" / "Total bytes host2: NNNN" (overall progress)
  - "Copying message N/M from folder FOLDER to FOLDER" (per-message progress)
  - "++++" / "...." banners (folder start/end)
  - "Detected N errors" / "The sync looks good" / "Exiting with return value N"

We expose a single async generator `run_sync(account, emit)` so the API layer
can pipe events to a WebSocket without blocking the event loop.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional


# ---- regexes pulled from real imapsync output (2.314) --------------------

RE_HOST1_MSG = re.compile(r"^Host1:\s*nb messages:\s*(\d+)", re.MULTILINE)
RE_HOST2_MSG = re.compile(r"^Host2:\s*nb messages:\s*(\d+)", re.MULTILINE)
RE_BYTES_H1 = re.compile(r"Total bytes host1:\s*(\d+)")
RE_BYTES_H2 = re.compile(r"Total bytes host2:\s*(\d+)")
RE_COPYING = re.compile(
    r"^Copying message\s+(\d+)\s*/\s*(\d+)\s+from folder\s+(.+?)\s+to\s+(.+?)\s*$",
    re.MULTILINE,
)
RE_FOLDER_LINE = re.compile(r"^(\+{2,}|\.{2,}|\*{2,})\s+(.+)$")
RE_EXIT = re.compile(r"Exiting with return value\s+(\d+)")
RE_DETECTED_ERRORS = re.compile(r"Detected\s+(\d+)\s+errors")
RE_SYNC_GOOD = re.compile(r"The sync looks good")
RE_FOLDER_COUNT = re.compile(r"^Folders count:\s*(\d+)", re.MULTILINE)
RE_BANNER = re.compile(r"^=+\s*(.*?)\s*=+\s*$")


@dataclass
class SyncOptions:
    """Mirrors the most-used imapsync flags the UI exposes."""

    nolog: bool = True
    ssl1: bool = False
    ssl2: bool = False
    tls1: bool = False
    tls2: bool = False
    delete1: bool = False
    delete2: bool = False
    automap: bool = False
    dry: bool = False
    extra: list[str] = field(default_factory=list)
    timeout1: Optional[float] = None
    timeout2: Optional[float] = None


@dataclass
class Account:
    """A source -> destination IMAP account pair."""

    id: str
    name: str
    host1: str
    port1: Optional[int] = None
    user1: str = ""
    password1: str = ""
    host2: str = ""
    port2: Optional[int] = None
    user2: str = ""
    password2: str = ""
    options: SyncOptions = field(default_factory=SyncOptions)


@dataclass
class SyncEvent:
    """A single streaming event from imapsync."""

    account_id: str
    type: str  # line | folder | progress | banner | exit | error | started | finished
    data: dict[str, Any]


Emit = Callable[[SyncEvent], Awaitable[None]]


def _find_imapsync() -> str:
    """Locate the imapsync binary. In container: /usr/local/bin/imapsync."""
    path = shutil.which("imapsync") or "/usr/local/bin/imapsync"
    if not Path(path).exists():
        raise FileNotFoundError(f"imapsync not found at {path}")
    return path


def build_command(account: Account, log_dir: Path) -> list[str]:
    """Build an imapsync argv. Uses --passfile* so passwords are not on argv
    (imapsync accepts passwords via short-lived files we delete right after)."""
    cmd = [_find_imapsync()]

    # Connection side 1
    cmd += ["--host1", account.host1]
    if account.port1:
        cmd += ["--port1", str(account.port1)]
    cmd += ["--user1", account.user1]
    # Password -> temp file (0600), deleted at end
    pwd1 = log_dir / f".{account.id}.pwd1"
    pwd1.write_text(account.password1 + "\n")
    os.chmod(pwd1, 0o600)
    cmd += ["--passfile1", str(pwd1)]

    # Connection side 2
    cmd += ["--host2", account.host2]
    if account.port2:
        cmd += ["--port2", str(account.port2)]
    cmd += ["--user2", account.user2]
    pwd2 = log_dir / f".{account.id}.pwd2"
    pwd2.write_text(account.password2 + "\n")
    os.chmod(pwd2, 0o600)
    cmd += ["--passfile2", str(pwd2)]

    # Options
    o = account.options
    if o.ssl1:
        cmd.append("--ssl1")
    elif o.tls1:
        cmd.append("--tls1")
    if o.ssl2:
        cmd.append("--ssl2")
    elif o.tls2:
        cmd.append("--tls2")
    if o.delete1:
        cmd.append("--delete1")
    if o.delete2:
        cmd.append("--delete2")
    if o.automap:
        cmd.append("--automap")
    if o.dry:
        cmd.append("--dry")
    if o.timeout1 is not None:
        cmd += ["--timeout1", str(o.timeout1)]
    if o.timeout2 is not None:
        cmd += ["--timeout2", str(o.timeout2)]

    # Logging
    log_file = log_dir / f"imapsync_{account.id}.log"
    cmd += ["--logfile", str(log_file)]
    if o.nolog:
        cmd.append("--nolog")  # redundant but harmless
    # pidfile for clean abort
    pidfile = log_dir / f".{account.id}.pid"
    cmd += ["--pidfile", str(pidfile)]

    if o.extra:
        cmd += o.extra

    return cmd, pwd1, pwd2, log_file


def _parse_line(account_id: str, line: str) -> Optional[SyncEvent]:
    """Map a single stdout line to a typed event. Returns None for chatter."""

    # Per-message progress
    m = RE_COPYING.match(line)
    if m:
        cur, total, src, dst = m.groups()
        return SyncEvent(
            account_id,
            "progress",
            {
                "current": int(cur),
                "total": int(total),
                "source_folder": src.strip(),
                "dest_folder": dst.strip(),
                "percent": round(int(cur) / int(total) * 100, 1) if int(total) else 0,
            },
        )

    # Folder markers
    m = RE_FOLDER_LINE.match(line)
    if m:
        marker, name = m.groups()
        kind = "folder_start" if marker.startswith("+") else "folder_end"
        return SyncEvent(account_id, "folder", {"name": name.strip(), "kind": kind})

    # Byte totals
    m = RE_BYTES_H1.search(line)
    if m:
        return SyncEvent(account_id, "size", {"side": "host1", "bytes": int(m.group(1))})
    m = RE_BYTES_H2.search(line)
    if m:
        return SyncEvent(account_id, "size", {"side": "host2", "bytes": int(m.group(1))})

    # Final stats
    m = RE_DETECTED_ERRORS.search(line)
    if m:
        return SyncEvent(account_id, "stat", {"errors": int(m.group(1))})
    if RE_SYNC_GOOD.search(line):
        return SyncEvent(account_id, "stat", {"sync_good": True})
    m = RE_EXIT.search(line)
    if m:
        return SyncEvent(account_id, "stat", {"exit_code": int(m.group(1))})

    return None


async def run_sync(
    account: Account,
    emit: Emit,
    log_dir: Path,
) -> int:
    """Spawn imapsync, stream events via `emit`, return exit code."""

    log_dir.mkdir(parents=True, exist_ok=True)
    cmd, pwd1, pwd2, log_file = build_command(account, log_dir)

    await emit(
        SyncEvent(account.id, "started", {"cmd_preview": " ".join(_safe_cmd(cmd))})
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )

    assert proc.stdout is not None
    # Track cumulative message progress across folders
    folder_total_seen = 0
    folder_current_seen = 0

    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\n")

            # Always emit raw line so UI can show a log pane
            await emit(SyncEvent(account.id, "line", {"text": line}))

            evt = _parse_line(account.id, line)
            if not evt:
                continue
            if evt.type == "progress":
                folder_current_seen = max(folder_current_seen, evt.data["current"])
                folder_total_seen = max(folder_total_seen, evt.data["total"])
                evt.data["folder_current"] = folder_current_seen
                evt.data["folder_total"] = folder_total_seen
            await emit(evt)

        rc = await proc.wait()
    finally:
        # Always scrub password files
        for f in (pwd1, pwd2):
            try:
                f.unlink()
            except FileNotFoundError:
                pass

    await emit(
        SyncEvent(
            account.id,
            "finished",
            {"exit_code": rc, "log_file": str(log_file)},
        )
    )
    return rc


def _safe_cmd(cmd: list[str]) -> list[str]:
    """Replace password file args with *** in any debug output."""
    out = []
    skip_next = False
    for tok in cmd:
        if skip_next:
            out.append("***")
            skip_next = False
            continue
        if tok in {"--passfile1", "--passfile2", "--password1", "--password2"}:
            out.append(tok)
            skip_next = True
            continue
        out.append(tok)
    return out


async def abort_sync(account_id: str, log_dir: Path) -> bool:
    """Send SIGTERM to a running sync via its pidfile."""
    pidfile = log_dir / f".{account_id}.pid"
    if not pidfile.exists():
        return False
    try:
        pid = int(pidfile.read_text().strip().split()[0])
        os.kill(pid, signal.SIGTERM)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False