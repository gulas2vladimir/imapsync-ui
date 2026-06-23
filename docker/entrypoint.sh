#!/bin/sh
set -e

# Sanity check imapsync is on PATH and runnable.
if ! command -v imapsync >/dev/null 2>&1; then
    echo "FATAL: imapsync not on PATH inside container" >&2
    exit 1
fi

echo "imapsync binary: $(command -v imapsync)"
echo "Starting imapsync web UI on 0.0.0.0:${PORT:-8000}"

# Drop privs if running as root in the container.
if [ "$(id -u)" = "0" ]; then
    mkdir -p /var/log/imapsync-ui
    chown -R imapui:imapui /var/log/imapsync-ui /app || true
    cd /app/backend
    exec setpriv --reuid=imapui --regid=imapui --init-groups \
        python -m uvicorn app:app \
            --host 0.0.0.0 --port "${PORT:-8000}" \
            --log-level info
fi

cd /app/backend
exec python -m uvicorn app:app \
    --host 0.0.0.0 --port "${PORT:-8000}" \
    --log-level info