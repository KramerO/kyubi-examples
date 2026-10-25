"""Pilot fahren (WP-P.1): Daten erzeugen, Jobnetz je Geschäftstag einplanen, Ergebnis prüfen.

    scripts/db-up.sh
    uv run python examples/dwh_pilot/run_pilot.py --days 2026-10-19,2026-10-20,2026-10-21

Nutzt die Testdatenbank aus ``dev/.env`` (Schema ``pilot`` wird neu angelegt), startet den
Scheduler im Prozess und plant je Tag das ganze Jobnetz über ``job.order`` ein (wie das
Control Center). Ergebnis: Laufzeiten je Job, Zeilenzahlen und Abgleich Quelle ↔ DWH.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
TERMINAL = {"succeeded", "failed", "cancelled"}


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for name in (".env.example", ".env"):
        path = REPO / "dev" / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip()
    return env


def dsn(test: dict[str, str]) -> str:
    return (
        f"host=127.0.0.1 port={test['KYUBI_TEST_PG_PORT']} user={test['KYUBI_TEST_PG_USER']} "
        f"password={test['KYUBI_TEST_PG_PASSWORD']} dbname={test['KYUBI_TEST_PG_DB']}"
    )


def op(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    from kyubi.ops.context import execute

    result = execute(ROOT, [(name, payload)], apply=True, permission="run")
    if not result.ok:
        problems = [f"{d.code} {d.message}" for r in result.results for d in r.diagnostics]
        raise SystemExit(f"{name}: {'; '.join(problems + [d.message for d in result.diagnostics])}")
    return result.results[0].data


def wait(service: Any, instance_id: str, limit: float) -> dict[str, Any]:
    store = service.machine.store
    started = time.monotonic()
    while time.monotonic() - started < limit:
        instance = store.instance(instance_id)
        if instance is not None and instance.state in TERMINAL:
            break
        time.sleep(1)
    instance = store.instance(instance_id)
    jobs = {
        a.job: {
            "state": a.state,
            "seconds": round(((a.ended_at or a.started_at) - a.started_at).total_seconds(), 1),
            "run_id": a.run_id,
            "message": a.message,
        }
        for a in store.attempts(instance_id)
    }
    return {"instance": instance_id, "state": instance.state if instance else "?", "jobs": jobs,
            "seconds": round(time.monotonic() - started, 1)}  # fmt: skip


def checks(test: dict[str, str], days: list[str], data: Path) -> dict[str, Any]:
    import psycopg
    import pyarrow.csv as pacsv

    out: dict[str, Any] = {}
    with psycopg.connect(dsn(test)) as conn:
        for day in days:
            stamp = day.replace("-", "")
            source = sum(
                pacsv.read_csv(p).num_rows
                for p in sorted((data / "in").glob(f"orders_{stamp}_*.csv"))
            )
            unmatched_file = data / "out" / f"orders_unmatched_{stamp}.csv"
            unmatched = pacsv.read_csv(unmatched_file).num_rows if unmatched_file.is_file() else 0
            rejected_file = data / "out" / f"orders_rejected_{stamp}.csv"
            rejected = pacsv.read_csv(rejected_file).num_rows if rejected_file.is_file() else 0
            facts, amount = conn.execute(
                "SELECT count(*), coalesce(sum(amount), 0) FROM pilot.fact_order "
                "WHERE order_date=%s",
                (day,),
            ).fetchone()  # type: ignore[misc]
            mart_orders, revenue = conn.execute(
                "SELECT coalesce(sum(orders), 0), coalesce(sum(revenue), 0) "
                "FROM pilot.mart_sales_day WHERE order_date=%s",
                (day,),
            ).fetchone()  # type: ignore[misc]
            out[day] = {
                "orders_source": source,
                "facts": facts,
                "unmatched": unmatched,
                "rejected": rejected,
                "facts_plus_unmatched_equals_source": facts + unmatched + rejected == source,
                "mart_orders_equals_facts": mart_orders == facts,
                "mart_revenue_equals_facts": revenue == amount,
                "revenue": str(revenue),
            }
        versions = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_current), "
            "count(DISTINCT customer_id), "
            "(SELECT count(*) FROM (SELECT customer_id FROM pilot.dim_customer WHERE is_current "
            " GROUP BY customer_id HAVING count(*) > 1) d) FROM pilot.dim_customer"
        ).fetchone()
        names = ("rows", "current", "customers", "duplicate_current")
        out["dim_customer"] = dict(zip(names, versions or (), strict=True))
        last = pacsv.read_csv(data / "in" / f"customers_{days[-1].replace('-', '')}.csv")
        columns = [last.column(c).to_pylist() for c in ("customer_id", "name", "email", "city")]
        snapshot = set(zip(*columns, strict=True))
        current = set(
            conn.execute(
                "SELECT customer_id, name, email, city FROM pilot.dim_customer WHERE is_current"
            ).fetchall()
        )
        out["dim_current_equals_last_snapshot"] = current == snapshot
        gaps = conn.execute(
            "SELECT count(*) FROM (SELECT valid_from, lag(valid_to) OVER w AS prev_to "
            "FROM pilot.dim_customer WINDOW w AS (PARTITION BY customer_id ORDER BY valid_from)) v "
            "WHERE prev_to IS NOT NULL AND prev_to <> valid_from - 1"
        ).fetchone()
        out["dim_history_without_gaps"] = gaps == (0,)  # Version endet am Vortag der nächsten
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", required=True)
    parser.add_argument("--orders", type=int, default=500_000)
    parser.add_argument("--customers", type=int, default=200_000)
    parser.add_argument("--work", type=Path, default=REPO / ".pilot")
    parser.add_argument("--report", type=Path, default=None, help="Ergebnis als JSON")
    args = parser.parse_args()
    days = [d.strip() for d in args.days.split(",")]
    test = load_env()
    data = args.work / "data"
    os.environ.update(
        {
            "KYUBI_PILOT_DATA": data.as_posix(),
            "KYUBI_SECRET_PILOT_PASSWORD": test["KYUBI_TEST_PG_PASSWORD"],
            "KYUBI_DATA_DIR": (args.work / "kyubi").as_posix(),
            "KYUBI_TEMP_DIR": (args.work / "tmp").as_posix(),
        }
    )
    import psycopg

    with psycopg.connect(dsn(test), autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS pilot CASCADE")
    for leftover in (data / "out").glob("*"):
        leftover.unlink()
    started = time.monotonic()
    subprocess.run(  # noqa: S603 - fester Befehl
        [sys.executable, str(ROOT / "generate.py"), "--days", ",".join(days), "--orders",
         str(args.orders), "--customers", str(args.customers), "--out", str(data / "in")],
        check=True,
    )  # fmt: skip
    generated = round(time.monotonic() - started, 1)
    from kyubi.scheduler.service import reset_services, service_for

    service = service_for(ROOT, dict(os.environ))
    service.start(0.5)
    report: dict[str, Any] = {"generate_seconds": generated, "orders_per_day": args.orders,
                              "customers": args.customers, "days": {}}  # fmt: skip
    try:
        setup = op("job.order", {"job": "pilot_setup", "mode": "now"})
        report["setup"] = wait(service, setup["instance"], 300)
        for day in days:
            ordered = op("job.order", {"job": "sales_mart", "mode": "net", "business_date": day})
            result = wait(service, ordered["instance"], 3600)
            report["days"][day] = result
            print(
                day,
                result["state"],
                {k: (v["state"], v["seconds"]) for k, v in result["jobs"].items()},
            )
            if result["state"] != "succeeded":
                break
    finally:
        service.stop()
        reset_services()
    report["checks"] = checks(test, days, data)
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
