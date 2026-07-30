#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="$PROJECT_ROOT/.venv/bin/python"
MANAGE_PY="$PROJECT_ROOT/web-ui/manage.py"
CHILD_PIDS=()

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  set +e

  for pid in "${CHILD_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid"
    fi
  done

  for pid in "${CHILD_PIDS[@]}"; do
    wait "$pid" 2>/dev/null
  done

  exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 130' INT TERM

"$PYTHON" "$MANAGE_PY" runserver "$@" &
DJANGO_PID=$!
CHILD_PIDS+=("$DJANGO_PID")

echo "Django 已启动，正在预启动并验收紫鸟店铺浏览器首页。"
if ! "$PYTHON" "$MANAGE_PY" prepare_ziniao_browser; then
  echo "紫鸟店铺首页暂未就绪；Django 和 Worker 将继续运行，请根据页面状态完成登录后重试。"
fi

"$PYTHON" "$MANAGE_PY" run_import_worker &
CHILD_PIDS+=("$!")

"$PYTHON" "$MANAGE_PY" run_creator_contact_worker --server-mode &
CHILD_PIDS+=("$!")

"$PYTHON" "$MANAGE_PY" run_collaboration_sync_worker &
CHILD_PIDS+=("$!")

echo "Django、店铺浏览器、达人导入 Worker、达人联系 Worker 和定向合作同步 Worker 已就绪。"
wait "$DJANGO_PID"
