"""Testdaten für den DWH-Piloten (WP-P.1): Kunden-Schnappschüsse, Produkte, Bestellungen.

    uv run python examples/dwh_pilot/generate.py --days 2026-10-19,2026-10-20 --orders 500000

Je Geschäftstag: ``customers_JJJJMMTT.csv`` (Vollbestand, ~1 % geändert, ~0,5 % neu, ~0,2 %
weg), ``orders_JJJJMMTT_<n>.csv`` (4 Dateien) und zuletzt ``orders_JJJJMMTT.ok`` (Trigger).
Einmalig ``products.parquet``. Zufall mit festem Startwert → reproduzierbar.
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

CITIES = ["Berlin", "Hamburg", "München", "Köln", "Frankfurt", "Stuttgart", "Leipzig", "Dresden"]
CATEGORIES = ["Bücher", "Elektronik", "Garten", "Haushalt", "Spielzeug", "Sport"]
PARTS = 4


def prices(n: int, rnd: random.Random) -> pa.Array:
    return pa.array([round(rnd.uniform(1, 500), 2) for _ in range(n)], pa.float64())


def products(target: Path, count: int, rnd: random.Random) -> None:
    pq.write_table(
        pa.table({
            "product_id": list(range(1, count + 1)),
            "product_name": [f"Artikel {i}" for i in range(1, count + 1)],
            "category": [rnd.choice(CATEGORIES) for _ in range(count)],
            "list_price": prices(count, rnd),
        }),
        target / "products.parquet",
    )  # fmt: skip


def evolve(customers: dict[int, list[str]], next_id: int, rnd: random.Random) -> int:
    """Ein Tag: Änderungen (Stadt/E-Mail), Zugänge, Abgänge."""
    ids = list(customers)
    for cid in rnd.sample(ids, len(ids) // 100):
        row = customers[cid]
        row[2] = rnd.choice(CITIES) if rnd.random() < 0.7 else row[2]
        row[1] = f"kunde{cid}.{rnd.randrange(1000)}@example.com"
    for cid in rnd.sample(ids, len(ids) // 500):
        del customers[cid]
    for _ in range(len(ids) // 200):
        customers[next_id] = [f"Kunde {next_id}", f"kunde{next_id}@example.com", rnd.choice(CITIES)]
        next_id += 1
    return next_id


def day_files(target: Path, day: dt.date, customers: dict[int, list[str]], orders: int,
              product_count: int, rnd: random.Random, first_order: int) -> int:  # fmt: skip
    stamp = f"{day:%Y%m%d}"
    ids = sorted(customers)
    pacsv.write_csv(
        pa.table({
            "customer_id": ids,
            "name": [customers[i][0] for i in ids],
            "email": [customers[i][1] for i in ids],
            "city": [customers[i][2] for i in ids],
        }),
        target / f"customers_{stamp}.csv",
    )  # fmt: skip
    per_part = orders // PARTS
    for part in range(PARTS):
        start = first_order + part * per_part
        n = per_part
        pacsv.write_csv(
            pa.table({
                "order_id": list(range(start, start + n)),
                "order_date": [day] * n,
                "customer_id": [rnd.choice(ids) if rnd.random() > 0.001 else 0 for _ in range(n)],
                "product_id": [rnd.randint(1, product_count) for _ in range(n)],
                "quantity": [rnd.randint(1, 5) for _ in range(n)],
                "unit_price": prices(n, rnd),
            }),
            target / f"orders_{stamp}_{part}.csv",
        )  # fmt: skip
    (target / f"orders_{stamp}.ok").write_text(f"{per_part * PARTS}\n", encoding="utf-8")
    return first_order + per_part * PARTS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", required=True, help="Geschäftstage, z. B. 2026-10-19,2026-10-20")
    parser.add_argument("--customers", type=int, default=200_000)
    parser.add_argument("--orders", type=int, default=500_000, help="Bestellungen je Tag")
    parser.add_argument("--products", type=int, default=5_000)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "data" / "in")
    args = parser.parse_args()
    rnd = random.Random(42)  # noqa: S311 - Testdaten
    args.out.mkdir(parents=True, exist_ok=True)
    products(args.out, args.products, rnd)
    customers = {
        i: [f"Kunde {i}", f"kunde{i}@example.com", rnd.choice(CITIES)]
        for i in range(1, args.customers + 1)
    }
    next_id, first_order = args.customers + 1, 1
    for n, text in enumerate(args.days.split(",")):
        day = dt.date.fromisoformat(text.strip())
        if n:
            next_id = evolve(customers, next_id, rnd)
        first_order = day_files(
            args.out, day, customers, args.orders, args.products, rnd, first_order
        )
        print(f"{day}: {len(customers)} Kunden, {args.orders} Bestellungen")


if __name__ == "__main__":
    main()
