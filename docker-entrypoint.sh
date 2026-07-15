#!/bin/sh
set -e
# optional Xvfb for headed Chromium
if [ "${HEADLESS:-false}" != "true" ]; then
  Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
  export DISPLAY=:99
  sleep 1
fi
exec uv run python -m grokreg "$@"
