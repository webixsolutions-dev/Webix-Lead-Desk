#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
.venv/bin/python -m app.cli migrate
exec .venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers

