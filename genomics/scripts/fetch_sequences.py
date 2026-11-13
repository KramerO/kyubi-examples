#!/usr/bin/env python3
"""M26 – Genomsequenzen (FASTA) von NCBI bzw. ENA ins Landing holen.

Je Eintrag in ``seeds/sequences.csv`` eine Sequenz. Zwei Dinge entstehen dabei:

* die **rohe FASTA-Datei** unter ``<out>/sequences/<accession>.fasta`` – sie bleibt liegen,
  damit niemand sie zweimal herunterlädt und damit man sie noch hat, wenn die Auswertung
  sich ändert,
* eine **Zeile je Sequenz** in der TSV: Beschreibung, Länge, GC-Anteil, Zahl der
  unbestimmten Basen (``N``) und ein ``sha256`` über die reine Sequenz.

Die Prüfsumme ist der Grund, warum das hier so gebaut ist: Dasselbe Genom kommt aus
mehreren Quellen (NCBI, ENA, ein Kaggle-Abzug derselben Datei). Nur die Prüfsumme sagt, ob
es wirklich dasselbe ist – die Nummer allein tut es nicht, denn Versionen (``MN908947.3``)
und Groß-/Kleinschreibung der Basen unterscheiden sich zwischen Abzügen.

Die Sequenz selbst steht **nicht** in der TSV: Ein Bakterienchromosom hat ein paar Millionen
Basen, das gehört nicht in eine Datenbankspalte. Die ersten 60 Basen fahren als Probe mit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HEADER = (
    "fetched_at", "accession", "source", "organism_taxon", "description",
    "length", "gc_count", "n_count", "sha256", "head", "file",
)  # fmt: skip
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ENA = "https://www.ebi.ac.uk/ena/browser/api/fasta"
BASES_GC = frozenset("GCgc")


def url_for(accession: str, source: str) -> str:
    if source == "ena":
        return f"{ENA}/{urllib.parse.quote(accession)}"
    params = urllib.parse.urlencode(
        {"db": "nuccore", "id": accession, "rettype": "fasta", "retmode": "text"}
    )
    return f"{NCBI}?{params}"


def parse_fasta(text: str) -> tuple[str, str]:
    """``>Kopfzeile\\nBasen…`` → (Beschreibung, Sequenz ohne Zeilenumbrüche)."""
    description = ""
    bases: list[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            if description:
                break  # nur der erste Datensatz je Datei – mehr erwartet dieses Beispiel nicht
            description = line[1:].strip()
        elif line.strip():
            bases.append(line.strip())
    return description, "".join(bases)


def statistics(sequence: str) -> tuple[int, int, int, str]:
    upper = sequence.upper()
    gc = sum(1 for base in sequence if base in BASES_GC)
    n = upper.count("N")
    digest = hashlib.sha256(upper.encode("ascii", errors="replace")).hexdigest()
    return len(sequence), gc, n, digest


def write_tsv(path: Path, data: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        handle.write("\t".join(HEADER) + "\n")
        for row in data:
            handle.write("\t".join(c.replace("\t", " ").replace("\n", " ") for c in row) + "\n")
        temp = Path(handle.name)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="Landing-Verzeichnis (je Geschäftstag)")
    parser.add_argument("--sequences", required=True, help="seeds/sequences.csv")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--refresh", action="store_true", help="auch schon vorhandene FASTA neu laden"
    )
    args = parser.parse_args()

    now = datetime.now(UTC)
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
    out = Path(args.out)
    store = out / "sequences"
    store.mkdir(parents=True, exist_ok=True)

    rows: list[list[str]] = []
    with Path(args.sequences).open(encoding="utf-8", newline="") as handle:
        wanted = list(csv.DictReader(handle))
    for entry in wanted:
        accession = (entry.get("accession") or "").strip()
        source = (entry.get("source") or "ncbi").strip()
        if not accession:
            continue
        target = store / f"{accession}.fasta"
        if args.refresh or not target.is_file():
            try:
                request = urllib.request.Request(  # noqa: S310 – feste https-Basis
                    url_for(accession, source), headers={"Accept": "text/plain"}
                )
                with urllib.request.urlopen(request, timeout=args.timeout) as response:  # noqa: S310
                    target.write_bytes(response.read())
            except (urllib.error.URLError, OSError) as exc:
                print(f"fetch_sequences: {accession} fällt aus ({type(exc).__name__})")
                continue
        description, sequence = parse_fasta(target.read_text(encoding="utf-8", errors="replace"))
        if not sequence:
            print(f"fetch_sequences: {accession} enthält keine Basen – ausgelassen")
            continue
        length, gc, n, digest = statistics(sequence)
        rows.append(
            [
                fetched_at,
                accession,
                source,
                (entry.get("organism_taxon") or "").strip(),
                description,
                str(length),
                str(gc),
                str(n),
                digest,
                sequence[:60],
                target.name,
            ]
        )
        print(f"fetch_sequences: {accession} {length} Basen, GC {gc * 100 // max(length, 1)} %")

    write_tsv(out / f"sequences_{now.strftime('%Y%m%dT%H%M%SZ')}__sequences.tsv", rows)
    print(f"fetch_sequences: {len(rows)} Sequenzen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
