#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pytest tests/unit -q
bash scripts/compose.sh run --rm --build database-tests
python3 scripts/business_test.py
