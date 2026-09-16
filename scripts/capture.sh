#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p artifacts/runtime
# Do not export compose config or inspect environment: both contain credentials.
bash scripts/compose.sh ps --format json > artifacts/runtime/containers.jsonl
bash scripts/compose.sh logs --no-color --tail=300 > artifacts/runtime/containers.log 2>&1
curl -fsS http://localhost:28080/api/business/snapshot > artifacts/runtime/snapshot.json || true
curl -fsS http://localhost:28080/api/business/alerts > artifacts/runtime/alerts.json || true
docker stats --no-stream --format '{{json .}}' $(bash scripts/compose.sh ps -q) > artifacts/runtime/resources.jsonl || true
git rev-parse HEAD > artifacts/runtime/commit.txt
if [ -d node_modules/playwright ]; then
  node scripts/capture-dashboard.mjs || true
fi
