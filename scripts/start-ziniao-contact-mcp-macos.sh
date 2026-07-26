#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT/ziniao-automation/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PROJECT_ROOT/.venv/bin/python" -m ziniao_automation.mcp_server
