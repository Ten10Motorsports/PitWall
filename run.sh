#!/usr/bin/env bash
# PitWall - start the server.
# Live telemetry needs Windows (that is iRacing's SDK, not a PitWall choice),
# but everything else - overlays, timing page, roster, demo mode - runs here.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python 3.9+ is required. Nothing else is."
  exit 1
fi
exec "$PY" -m server.main "$@"
