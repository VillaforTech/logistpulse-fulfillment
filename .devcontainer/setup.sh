#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
python3 scripts/configure.py
printf 'Reference ready. Start with bash scripts/up.sh; console port 28080.\n'
