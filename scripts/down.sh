#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/compose.sh down
# Named volumes retain domain facts, projections and offsets. No implicit deletion.
