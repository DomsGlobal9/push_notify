#!/usr/bin/env bash
# Production entry point on Render.
#
# RUN_DISPATCHER_INLINE=true (default, the cheap MVP setup): the dispatcher runs
# inside the web service container and is restarted if it ever exits.
# Set it to false once the separate `push-dispatcher` background worker from
# render.yaml is running.
set -u

DISPATCHER_PID=""
WEB_PID=""

# Apply migrations (and the Supabase API lockdown) before serving. This replaces
# Render's pre-deploy command, which is not offered on every plan/form.
if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  python manage.py migrate --noinput || { echo "migrate failed; not starting" >&2; exit 1; }
  python manage.py ensure_admin_user || true
fi

shutdown() {
  [ -n "$WEB_PID" ] && kill -TERM "$WEB_PID" 2>/dev/null
  [ -n "$DISPATCHER_PID" ] && kill -TERM "$DISPATCHER_PID" 2>/dev/null
  wait
  exit "${1:-0}"
}
trap 'shutdown 0' TERM INT

if [ "${RUN_DISPATCHER_INLINE:-true}" = "true" ]; then
  (
    child=""
    trap '[ -n "$child" ] && kill -TERM "$child" 2>/dev/null; wait; exit 0' TERM INT
    while true; do
      python manage.py run_dispatcher &
      child=$!
      wait "$child"
      echo "dispatcher exited ($?); restarting in 5s" >&2
      sleep 5
    done
  ) &
  DISPATCHER_PID=$!
fi

gunicorn gateway.wsgi:application -c gunicorn.conf.py &
WEB_PID=$!

# If gunicorn exits on its own, stop everything so Render restarts the service.
wait "$WEB_PID"
WEB_PID=""
echo "gunicorn exited; stopping" >&2
shutdown 1
