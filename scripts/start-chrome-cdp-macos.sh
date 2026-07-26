#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHROME_BINARY="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT="${CHROME_CDP_PORT:-9333}"
PROFILE_DIR="$PROJECT_ROOT/temporary/chrome-cdp-profile"
TARGET_URL="https://www.chuhaijiang.com/app/discover/tiktok/products?country=US"
ENCODED_TARGET="https%3A%2F%2Fwww.chuhaijiang.com%2Fapp%2Fdiscover%2Ftiktok%2Fproducts%3Fcountry%3DUS"

if [[ ! -x "$CHROME_BINARY" ]]; then
  echo "未找到 Google Chrome：$CHROME_BINARY"
  exit 1
fi

mkdir -p "$PROFILE_DIR"
if ! curl --silent --fail --max-time 1 \
  "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null; then
  open -na "Google Chrome" --args \
    --remote-debugging-port="$CDP_PORT" \
    --user-data-dir="$PROFILE_DIR" \
    "$TARGET_URL"
fi

for attempt in {1..15}; do
  if curl --silent --fail --max-time 1 \
    "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null; then
    if ! curl --silent "http://127.0.0.1:$CDP_PORT/json/list" \
      | grep -q "chuhaijiang.com"; then
      curl --silent --request PUT \
        "http://127.0.0.1:$CDP_PORT/json/new?$ENCODED_TARGET" >/dev/null
    fi
    echo "Chrome CDP 已启动：http://127.0.0.1:$CDP_PORT"
    exit 0
  fi
  sleep 1
done

echo "Chrome CDP 启动超时"
exit 1
