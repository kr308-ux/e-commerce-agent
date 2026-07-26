#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
npm install
npm run build

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

.venv/bin/python web-ui/manage.py check
.venv/bin/python web-ui/manage.py migrate

echo "环境准备完成。分别运行 Django 和 Worker："
echo ".venv/bin/python web-ui/manage.py runserver"
echo "./scripts/start-worker-macos.sh"
