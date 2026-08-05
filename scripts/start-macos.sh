#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="$PROJECT_ROOT/.venv/bin/python"
MANAGE_PY="$PROJECT_ROOT/web-ui/manage.py"

exec "$PYTHON" "$MANAGE_PY" run_runtime_supervisor "$@"
