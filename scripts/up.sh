#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/configure.py
bash scripts/compose.sh up -d --build --wait --wait-timeout 180
bash scripts/compose.sh ps
