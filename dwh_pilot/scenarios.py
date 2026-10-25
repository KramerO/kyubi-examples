"""Betriebsszenarien des Piloten (WP-P.1): Wiederanlauf, verspätete Datei, fehlerhafte Datei.

    uv run python examples/dwh_pilot/scenarios.py

S2 Tag erneut einplanen → gleiches Ergebnis, keine Duplikate.
S3 Trigger-Datei fehlt → Job wartet sichtbar, läuft nach Eintreffen weiter.
S4 Fehlerhafte Bestelldatei → Job scheitert (nach Retry), Webhook meldet; Datei korrigieren,
   ``rerun_failed`` → Ergebnis vollständig und genau einmal.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_pilot import REPO, ROOT, dsn, load_env, op, wait

DAYS = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]


class Hook:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        events = self.events

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                events.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"


def facts(test: dict[str, str], day: str) -> int:
    import psycopg

    with psycopg.connect(dsn(test)) as conn:
        row = conn.execute("SELECT count(*) FROM pilot.fact_order WHERE order_date=%s", (day,))
        return int(row.fetchone()[0])  # type: ignore[index]


def dim_rows(test: dict[str, str]) -> int:
    import psycopg

    with psycopg.connect(dsn(test)) as conn:
        return int(conn.execute("SELECT count(*) FROM pilot.dim_customer").fetchone()[0])  # type: ignore[index]


def job_state(service: Any, instance: str, job: str) -> tuple[str, str | None]:
    found = next(j for j in service.machine.store.jobs(instance) if j.name == job)
    return found.state, found.message


def main() -> None:
    test = load_env()
    work = REPO / ".pilot-scenarios"
    data = work / "data"
    hook = Hook()
    os.environ.update({
        "KYUBI_PILOT_DATA": data.as_posix(),
        "KYUBI_SECRET_PILOT_PASSWORD": test["KYUBI_TEST_PG_PASSWORD"],
        "KYUBI_DATA_DIR": (work / "kyubi").as_posix(),
        "KYUBI_TEMP_DIR": (work / "tmp").as_posix(),
        "KYUBI_PILOT_HOOK": hook.url,
    })  # fmt: skip
    import psycopg

    with psycopg.connect(dsn(test), autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS pilot CASCADE")
    shutil.rmtree(work, ignore_errors=True)
    from generate import main as generate

    sys.argv = ["generate", "--days", ",".join(DAYS), "--orders", "40000", "--customers", "20000",
                "--out", str(data / "in")]  # fmt: skip
    generate()
    (data / "out").mkdir(parents=True, exist_ok=True)
    from kyubi.scheduler.service import reset_services, service_for

    service = service_for(ROOT, dict(os.environ))
    service.start(0.5)
    result: dict[str, Any] = {}
    job_file = ROOT / "jobs" / "load_orders.job.yaml"
    original_job = job_file.read_text(encoding="utf-8")
    try:
        wait(service, op("job.order", {"job": "pilot_setup", "mode": "now"})["instance"], 120)
        for day in DAYS[:2]:
            first = wait(
                service,
                op("job.order", {"job": "sales_mart", "mode": "net", "business_date": day})[
                    "instance"
                ],
                600,
            )
            if first["state"] != "succeeded":
                raise SystemExit(f"Grundlauf {day} gescheitert: {first}")
        # S2 Wiederanlauf
        before = (facts(test, DAYS[1]), dim_rows(test))
        again = wait(
            service,
            op("job.order", {"job": "sales_mart", "mode": "net", "business_date": DAYS[1]})[
                "instance"
            ],
            600,
        )
        after = (facts(test, DAYS[1]), dim_rows(test))
        result["S2_rerun"] = {"state": again["state"], "facts_and_dim_unchanged": before == after,
                              "facts": after[0], "dim_rows": after[1]}  # fmt: skip

        # S3 verspätete Trigger-Datei
        stamp = DAYS[2].replace("-", "")
        marker = data / "in" / f"orders_{stamp}.ok"
        held = marker.read_text(encoding="utf-8")
        marker.unlink()
        late = op("job.order", {"job": "sales_mart", "mode": "net", "business_date": DAYS[2]})[
            "instance"
        ]
        waited = None
        for _ in range(90):
            state, message = job_state(service, late, "load_orders")
            if state == "waiting" and message and ".ok" in message:
                waited = message
                break
            time.sleep(1)
        marker.write_text(held, encoding="utf-8")
        finished = wait(service, late, 600)
        result["S3_late_file"] = {"waiting_message": waited, "state": finished["state"],
                                  "facts": facts(test, DAYS[2])}  # fmt: skip

        # S4 fehlerhafte Datei → Fehler, Webhook, korrigieren, rerun_failed
        job_file.write_text(original_job.replace("delay: 5m", "delay: 3s"), encoding="utf-8")
        stamp = DAYS[3].replace("-", "")
        part = data / "in" / f"orders_{stamp}_2.csv"
        good = part.read_text(encoding="utf-8")
        lines = good.splitlines()
        for i in range(1, 60):  # Menge "x" → Zeile nicht lesbar
            cells = lines[i].split(",")
            cells[4] = '"x"'
            lines[i] = ",".join(cells)
        part.write_text("\n".join(lines) + "\n", encoding="utf-8")
        broken = op("job.order", {"job": "sales_mart", "mode": "net", "business_date": DAYS[3]})[
            "instance"
        ]
        failed = wait(service, broken, 600)
        partial = facts(test, DAYS[3])
        time.sleep(2)  # Zustellung im Hintergrund
        events = [(e["event"], e.get("job")) for e in hook.events]
        part.write_text(good, encoding="utf-8")
        op("scheduler.action", {"instance": broken, "action": "rerun_failed"})
        repaired = wait(service, broken, 600)
        result["S4_bad_file"] = {
            "state_after_error": failed["state"],
            "error": next(
                (j["message"] for j in failed["jobs"].values() if j["state"] == "failed"), None
            ),
            "facts_after_error": partial,
            "webhook_events": events,
            "state_after_rerun": repaired["state"],
            "facts_after_rerun": facts(test, DAYS[3]),
        }

        # S5 wenige falsche Zeilen → abgelehnt (Fehlerdatei mit Zeilennummer), Lauf erfolgreich
        stamp = DAYS[4].replace("-", "")
        part = data / "in" / f"orders_{stamp}_1.csv"
        lines = part.read_text(encoding="utf-8").splitlines()
        for i in (10, 20, 30):
            cells = lines[i].split(",")
            cells[4] = '"x"'
            lines[i] = ",".join(cells)
        part.write_text("\n".join(lines) + "\n", encoding="utf-8")
        few = wait(
            service,
            op("job.order", {"job": "sales_mart", "mode": "net", "business_date": DAYS[4]})[
                "instance"
            ],
            600,
        )
        rejected = (
            (data / "out" / f"orders_rejected_{stamp}.csv")
            .read_text(encoding="utf-8")
            .splitlines()[1:]
        )
        result["S5_few_bad_rows"] = {
            "state": few["state"],
            "rejected_rows": len(rejected),
            "rejected_lines": sorted(int(r.split(",")[2]) for r in rejected),
            "facts": facts(test, DAYS[4]),
        }
    finally:
        job_file.write_text(original_job, encoding="utf-8")
        service.stop()
        reset_services()
        hook.server.shutdown()
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    (REPO / "docs" / "pilot" / "scenarios.json").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
