#!/bin/sh
# Entry point declared in .mcp.json.
#
# The server needs Python 3.11+ with PyYAML. A bare `python3` finds the 3.9 that
# ships with the macOS Command Line Tools on a stock machine, so the plugin
# would install, start, and die on import. Resolve a real interpreter here and
# fail with the requirement rather than a traceback.
set -e
ROOT=${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

for candidate in \
    "$TAXAGENT_PYTHON" \
    "$ROOT/.venv/bin/python3" \
    /opt/homebrew/bin/python3 \
    /opt/anaconda3/bin/python3 \
    /usr/local/bin/python3 \
    python3
do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys, yaml; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
         >/dev/null 2>&1; then
        exec "$candidate" -m taxagent.gateway.mcp_server "$@"
    fi
done

echo "tax-agent needs Python 3.11+ with PyYAML. Set TAXAGENT_PYTHON to one." >&2
exit 1
