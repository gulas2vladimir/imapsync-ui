# docker/

Scripts that run inside the final container.

## Owns

- `entrypoint.sh` — sanity-checks imapsync, drops to the `imapui` non-root
  user via `setpriv`, and execs `uvicorn app:app` from `/app/backend`.

## Work Guidance

- The entrypoint is invoked under `tini` (PID 1 in the runtime stage).
  Don't add `exec` chains that fork unnecessarily; uvicorn should be the
  direct child of tini for clean signal forwarding.
- Privilege drop uses `setpriv --reuid=imapui --regid=imapui --init-groups`.
  Do not use `su` or `gosu`; the runtime image does not include them.
- The entrypoint's `cd /app/backend` is load-bearing: `uvicorn` is invoked
  as `app:app` (not `backend.app:app`), so the module must be on the
  current directory of the process.
- Logs go to stdout; the entrypoint prints the imapsync version and
  the bind address so the first lines of `docker logs` always answer
  "is the tool actually there?".

## Verification

- `docker run --rm imapsync-ui:latest` should print the imapsync version
  and the uvicorn banner on stdout.
- Healthcheck in `docker-compose.yml` curls `/api/health` and expects
  `{"status":"ok"}` within 5 s.