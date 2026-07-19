#!/usr/bin/env python3
"""Run Alembic migrations across the control DB and every registered tenant DB.

Today the registry is empty, so this migrates exactly one database. The loop is
here so that when orgs move to dedicated databases (the growth switch), a single
`python migrate_all.py` keeps them all on the same schema — no rewrite needed.

Usage:  DATABASE_URL=postgresql://... python migrate_all.py [upgrade head]
Runs in CI / the deploy step / the compose `migrate` one-shot — NEVER per app
instance at startup (that would race across replicas).
"""
from __future__ import annotations

import os
import subprocess
import sys

import psycopg


def _targets(control_url: str) -> list[tuple[str, str]]:
    targets = [("control", control_url)]
    try:
        with psycopg.connect(control_url, connect_timeout=10) as conn:
            reg = conn.execute("SELECT to_regclass('public.tenant_databases')").fetchone()
            if reg and reg[0]:
                for org_id, dsn in conn.execute(
                        "SELECT org_id, dsn FROM tenant_databases WHERE active").fetchall():
                    targets.append((f"org:{org_id}", dsn))
    except Exception as e:  # noqa: BLE001
        # First run: control DB has no tables yet. Migrate control only.
        print(f"[migrate_all] registry not readable yet ({e}); control only")
    return targets


def main() -> int:
    control_url = os.environ.get("DATABASE_URL", "")
    if not control_url:
        sys.exit("DATABASE_URL is required")
    args = sys.argv[1:] or ["upgrade", "head"]
    here = os.path.dirname(os.path.abspath(__file__))

    failures = 0
    for label, dsn in _targets(control_url):
        print(f"[migrate_all] {label}: alembic {' '.join(args)}")
        env = {**os.environ, "MIGRATE_DATABASE_URL": dsn}
        rc = subprocess.run(["alembic", *args], cwd=here, env=env).returncode
        if rc != 0:
            print(f"[migrate_all] FAILED for {label} (rc={rc})")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
