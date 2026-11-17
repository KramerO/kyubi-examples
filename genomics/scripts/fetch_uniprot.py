#!/usr/bin/env python3
"""M26 – Geprüfte Proteine aus UniProt/SwissProt ins Landing holen.

SwissProt ist der **von Hand geprüfte** Teil von UniProt; genau deshalb ist er als Quelle
interessant: Jede Zeile ist kuratiert, die Namen sind stabil, und die Organismen tragen
ihre Taxon-Nummer mit – den Schlüssel, an dem sich dieser Datenstrang später mit den
Genomsequenzen trifft.

Gelesen wird die REST-Schnittstelle (`rest.uniprot.org`), ohne Zugangsdaten. Sie liefert
TSV und seitenweise; die nächste Seite steht als ``Link: <…>; rel="next"`` im Kopf. Das
Skript folgt diesen Verweisen, bis nichts mehr kommt oder ``--max-rows`` erreicht ist –
eine Abfrage wie „alle geprüften Proteine des Menschen" sind rund 20 000 Zeilen, und das
soll niemand aus Versehen in ein Beispielprojekt ziehen.

Je Organismus aus ``seeds/organisms.csv`` eine Abfrage, alles in **eine** TSV-Datei mit
Kopfzeile. Fällt das Netz aus, schreibt das Skript, was es hat, und endet mit 0: eine
Quelle, die heute schweigt, darf den Lauf nicht scheitern lassen.
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# Feld bei UniProt → Spalte in der Ausgabe. Die Reihenfolge ist die Spaltenfolge, und die
# Namen rechts sind die des Schemas `uniprot_raw`: UniProt nennt die Funktionsbeschreibung
# `cc_function` und die Domänen `ft_domain`, was außerhalb von UniProt niemand liest.
COLUMNS = (
    ("accession", "accession"),
    ("id", "entry_name"),
    ("protein_name", "protein_name"),
    ("gene_primary", "gene_primary"),
    ("organism_name", "organism_name"),
    ("organism_id", "organism_id"),
    ("length", "length"),
    ("mass", "mass"),
    ("cc_function", "function_text"),
    ("ft_domain", "domains"),
    ("reviewed", "reviewed"),
)
FIELDS = tuple(uniprot for uniprot, _ in COLUMNS)
HEADER = ("fetched_at", "taxon_id", *(column for _, column in COLUMNS))
BASE = "https://rest.uniprot.org/uniprotkb/search"
PAGE = 500


def next_link(headers: object) -> str | None:
    """``Link: <https://…>; rel="next"`` – der Weg zur nächsten Seite."""
    value = getattr(headers, "get", lambda _: None)("Link")
    if not value or 'rel="next"' not in value:
        return None
    start, end = value.find("<"), value.find(">")
    return value[start + 1 : end] if 0 <= start < end else None


def fetch(url: str, timeout: int) -> tuple[list[str], str | None]:
    request = urllib.request.Request(url, headers={"Accept": "text/plain"})  # noqa: S310 – feste https-Basis
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        text = response.read().decode("utf-8", errors="replace")
        return text.splitlines(), next_link(response.headers)


def organism_rows(
    taxon: str, query: str, fetched_at: str, limit: int, timeout: int
) -> list[list[str]]:
    params = urllib.parse.urlencode(
        {"query": query, "format": "tsv", "size": PAGE, "fields": ",".join(FIELDS)}
    )
    url: str | None = f"{BASE}?{params}"
    rows: list[list[str]] = []
    while url and len(rows) < limit:
        lines, url = fetch(url, timeout)
        for line in lines[1:]:  # Kopfzeile je Seite überspringen
            if not line.strip():
                continue
            values = line.split("\t")
            # Fehlende hintere Spalten auffüllen: UniProt lässt leere Felder am Ende weg
            values += [""] * (len(FIELDS) - len(values))
            rows.append([fetched_at, taxon, *values[: len(FIELDS)]])
            if len(rows) >= limit:
                break
    return rows


def write_tsv(path: Path, header: tuple[str, ...], data: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        handle.write("\t".join(header) + "\n")
        for row in data:
            handle.write("\t".join(c.replace("\t", " ").replace("\n", " ") for c in row) + "\n")
        temp = Path(handle.name)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="Landing-Verzeichnis (je Geschäftstag)")
    parser.add_argument("--organisms", required=True, help="seeds/organisms.csv")
    parser.add_argument("--max-rows", type=int, default=2000, help="Obergrenze je Organismus")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    now = datetime.now(UTC)
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
    target = Path(args.out) / f"uniprot_{now.strftime('%Y%m%dT%H%M%SZ')}__proteins.tsv"

    rows: list[list[str]] = []
    with Path(args.organisms).open(encoding="utf-8", newline="") as handle:
        organisms = list(csv.DictReader(handle))
    for organism in organisms:
        taxon = (organism.get("taxon_id") or "").strip()
        query = (organism.get("uniprot_query") or "").strip()
        if not taxon or not query:
            continue
        try:
            found = organism_rows(taxon, query, fetched_at, args.max_rows, args.timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"fetch_uniprot: {organism.get('label', taxon)} fällt aus ({type(exc).__name__})")
            continue
        print(f"fetch_uniprot: {organism.get('label', taxon)} → {len(found)} Proteine")
        rows += found

    write_tsv(target, HEADER, rows)
    print(f"fetch_uniprot: {len(rows)} Zeilen → {target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
