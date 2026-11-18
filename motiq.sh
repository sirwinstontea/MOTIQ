#!/usr/bin/env bash
# MOTIQ without the installer (macOS / Linux): ./motiq.sh   (--setup, --stop)
# First run opens the setup wizard in your browser; after that it starts MOTIQ.
cd "$(dirname "$0")"
for py in python3 python; do
  if command -v "$py" >/dev/null && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    exec "$py" launcher/motiq.py "$@"
  fi
done
if command -v uv >/dev/null; then exec uv run --no-project --python 3.12 launcher/motiq.py "$@"; fi
echo "MOTIQ needs Python 3.10+ to start its setup: https://www.python.org/downloads/" >&2
exit 1
