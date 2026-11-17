#!/usr/bin/env bash
# Local development: API on :8000 and the dispatcher, against local Postgres `push_gateway`.
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'kill $(jobs -p) 2>/dev/null' EXIT
export DEBUG=True
export LOCAL_DB_USER="${LOCAL_DB_USER:-$(id -un)}"
source .venv/bin/activate
python manage.py migrate --noinput
python manage.py run_dispatcher &
python manage.py runserver "0.0.0.0:${1:-8000}"
