"""Ground truth: did the persona really reach the goal? Asked of the shop's database, read-only,
never of the model. A persona saying "DONE" without an order in the database is a failure."""

import os
import subprocess

DB = os.environ.get("FOOTFALL_STORE_DB", "postgres://postgres@localhost:5442/medusa-dtc")


def query(sql, **params):
    """Run one read-only query with psql variables (:'name' in the SQL), return rows."""
    argv = ["psql", DB, "-At", "-v", "ON_ERROR_STOP=1",
            "-c", "set default_transaction_read_only = on", "-f", "-"]
    for key, value in params.items():
        argv[1:1] = ["-v", f"{key}={value}"]
    out = subprocess.run(argv, input=sql, capture_output=True, text=True, timeout=20)
    if out.returncode:
        raise RuntimeError(out.stderr.strip())
    # The first line is the SET's "SET" acknowledgement.
    return [line.split("|") for line in out.stdout.splitlines() if line and line != "SET"]


def check(success, email, start):
    rows = query(success["sql"], email=email, start=f"{start:.3f}")
    values = [cell for row in rows for cell in row]
    return {"ok": success["expect"] in values, "found": values, "expected": success["expect"],
            "describe": success["describe"]}
