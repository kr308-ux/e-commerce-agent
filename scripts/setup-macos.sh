#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

.venv/bin/python web-ui/manage.py check
.venv/bin/python web-ui/manage.py migrate

echo "环境准备完成。运行以下脚本可同时启动 Django、导入 Worker 和两个联系模块 Worker："
echo "./scripts/start-macos.sh"
