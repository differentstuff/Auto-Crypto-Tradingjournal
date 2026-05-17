#!/bin/bash
# MCP launcher for chrome-devtools-mcp.
# Finds node from common Claude Code installation paths, then runs the MCP server.
# Connects to a Chrome instance on port 9222 (launch Chrome first with:
#   open -a 'Google Chrome' --args --remote-debugging-port=9222
#                                   --user-data-dir=/tmp/claude-chrome-debug
#                                   'http://192.168.1.21:8082' )

set -euo pipefail

# Locate node binary — check common paths in priority order
for dir in \
    "$HOME/.openclaw/tools/node-v22.22.0/bin" \
    "$HOME/.openclaw/tools/node-v22.*/bin" \
    "$HOME/.nvm/versions/node/v*/bin" \
    "/opt/homebrew/bin" \
    "/usr/local/bin"; do
    # expand globs
    for expanded in $dir; do
        if [ -x "$expanded/node" ]; then
            export PATH="$expanded:$PATH"
            break 2
        fi
    done
done

if ! command -v npx &>/dev/null; then
    echo "ERROR: npx not found. Install Node.js 18+ and ensure it is on PATH." >&2
    exit 1
fi

exec npx chrome-devtools-mcp@latest --browserUrl=http://127.0.0.1:9222 "$@"
