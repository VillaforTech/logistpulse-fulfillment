#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker compose -p logistpulse-reference -f compose.yaml -f observability/compose.yaml "$@"
