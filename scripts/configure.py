#!/usr/bin/env python3
"""Create local-only credentials without replacing existing values or printing secrets."""
from pathlib import Path
import os, secrets

path = Path(".env")
values = (
    dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    if path.exists()
    else {}
)
for key in ("POSTGRES_PASSWORD", "ANALYTICS_PASSWORD", "GF_SECURITY_ADMIN_PASSWORD"):
    values.setdefault(key, secrets.token_hex(24))
values.setdefault("POSTGRES_USER", "logist")
values.setdefault("GF_SECURITY_ADMIN_USER", "admin")
if os.getenv("CODESPACE_NAME"):
    values.setdefault(
        "PUBLIC_BASE_URL",
        f"https://{os.environ['CODESPACE_NAME']}-28080.{os.getenv('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN','app.github.dev')}",
    )
path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
path.chmod(0o600)
print("Local .env configured; credentials were not printed.")
