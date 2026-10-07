#!/bin/sh
# Launch the local dashboard with an interpreter that can actually run it.
#
# The backend needs Python 3.11+ and PyYAML. On macOS the `python3` on PATH is
# often the 3.9 shipped with the Command Line Tools, which has neither, so the
# server would exit before binding the port. Pick the first interpreter that
# satisfies both and fail loudly with the requirement if none does.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)

for candidate in \
    "$TAXAGENT_PYTHON" \
    "$HERE/../.venv/bin/python3" \
    /opt/homebrew/bin/python3 \
    /opt/anaconda3/bin/python3 \
    /usr/local/bin/python3 \
    python3
do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys, yaml; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
         >/dev/null 2>&1; then
        exec "$candidate" "$HERE/serve_dashboard.py" "$@"
    fi
done

echo "No usable interpreter found: need Python 3.11+ with PyYAML." >&2
echo "Set TAXAGENT_PYTHON to one, or: python3 -m pip install pyyaml" >&2
exit 1
