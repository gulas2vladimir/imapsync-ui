# imapsync-web

Web UI + Docker image for `imapsync` (Gilles LAMIRAL's Perl IMAP migration tool).

The Perl script at `./imapsync` is vendored upstream, unmodified.

## Purpose

- Run `imapsync` from a modern browser, with live progress and multi-account input.
- Ship a single self-contained Docker image (`imapsync-ui`) with the Perl tool,
  every CPAN dep it needs, and the Python web UI.

## Ownership

- Container image, backend, frontend, docs: project root.
- Upstream Perl source: `./imapsync` — leave it untouched; rebuild via upstream releases.

## Local Contracts

- **Backend**: FastAPI + uvicorn. One process, asyncio. imapsync runs as a child
  subprocess per account. Passwords go through `--passfile*` (0600) and are deleted
  on run end.
- **Frontend**: single static HTML + ES module. Tailwind via CDN. No build step.
- **Docker**: multi-stage Debian Bookworm + Python 3.12 base. Non-root runtime
  user `imapui`. Logs live in `/var/log/imapsync-ui` (mounted volume).
- **Progress events**: WebSocket JSON `{type, account_id, data}`. Types:
  `started | line | folder | progress | size | stat | finished | run_finished`.

## Work Guidance

- The Perl script is upstream code. Do not edit `./imapsync`. To bump version,
  drop the new file in place and rebuild the image.
- Docker image must stay under ~700 MB. The `perl-build` stage validates the
  script can `--version` before shipping it; if that step fails the build fails.
- All Perl CPAN modules installed via `cpanm` (File::Tail, IO::Tee). Anything
  available as a Debian `lib*-perl` package must come from apt, not cpanm.
- Passwords never on argv. Always `--passfile*`. Files mode 0600, deleted after.
- Run state lives in memory; container restart wipes it. Persisted logs in the
  mounted volume survive.

## Verification

- `docker build -t imapsync-ui:test .` must succeed end-to-end.
- `docker compose up -d` must report `health: healthy` within ~30 s.
- `curl http://localhost:8000/api/health` returns `{"status":"ok"}`.
- WebSocket smoke test (see README) emits progress events with a fake binary.

## Child DOX Index

| Path           | Owns                                              |
|----------------|---------------------------------------------------|
| `backend/`     | FastAPI app, imapsync runner, Python deps        |
| `frontend/`    | Static HTML + JS UI, served by FastAPI           |
| `docker/`      | Entrypoint, runtime scripts inside container     |

Top-level files (`Dockerfile`, `docker-compose.yml`, `README.md`, `.gitignore`)
are owned by the root doc; each is a durable contract for the image as a whole.