#!/usr/bin/env python3
"""M26 – Datensätze von Kaggle ins Landing holen (nur mit Zugangsdaten).

Kaggle verlangt ein Konto; ohne Zugangsdaten gibt es keine Datei. Das Skript sucht sie in
dieser Reihenfolge:

1. ``KAGGLE_USERNAME`` und ``KAGGLE_KEY`` in der Umgebung,
2. ``KYUBI_SECRET_KAGGLE_USERNAME`` / ``KYUBI_SECRET_KAGGLE_KEY`` (Kyubi-Schreibweise,
   wird aus Logs und Berichten geschwärzt),
3. ``~/.kaggle/kaggle.json`` – die Datei, die Kaggle selbst anlegt.

Fehlen sie, schreibt das Skript eine leere Datei, sagt in einem Satz, was zu tun wäre, und
endet mit **0**: Ein Beispielprojekt darf nicht daran scheitern, dass jemand kein
Kaggle-Konto hat.

Welche Datensätze geholt werden, steht in ``seeds/kaggle.csv`` – mit einer Spalte
``enabled``. Das menschliche Referenzgenom (GRCh38) ist dort bewusst **aus**: Es sind rund
drei Gigabyte, und die will niemand versehentlich in ein Beispielprojekt ziehen.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

HEADER = ("fetched_at", "dataset", "file", "bytes", "rows", "note")
API = "https://www.kaggle.com/api/v1/datasets/download"


def credentials() -> tuple[str, str] | None:
    env = os.environ
    for user_key, token_key in (
        ("KAGGLE_USERNAME", "KAGGLE_KEY"),
        ("KYUBI_SECRET_KAGGLE_USERNAME", "KYUBI_SECRET_KAGGLE_KEY"),
    ):
        if env.get(user_key) and env.get(token_key):
            return env[user_key], env[token_key]
    path = Path(env.get("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle")) / "kaggle.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if data.get("username") and data.get("key"):
        return str(data["username"]), str(data["key"])
    return None


def extract(archive_path: Path, into: Path) -> int:
    """ZIP auspacken – jeden Eintrag erst prüfen.

    Ein heruntergeladenes Archiv ist fremde Eingabe: Ein Eintrag namens ``../../x`` oder ein
    absoluter Pfad würde bei ``extractall`` außerhalb des Zielordners landen. Deshalb hier
    Eintrag für Eintrag, und jeder Pfad muss nach dem Auflösen unter ``into`` liegen.
    """
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()
    written = 0
    with zipfile.ZipFile(archive_path) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            destination = (root / member.filename).resolve()
            if not destination.is_relative_to(root):
                print(f"fetch_kaggle: Eintrag '{member.filename}' zeigt aus dem Ordner heraus")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, destination.open("wb") as out:
                while chunk := source.read(1 << 20):
                    out.write(chunk)
            written += 1
    return written


def download(
    dataset: str, file_name: str, auth: tuple[str, str], target: Path, timeout: int
) -> int:
    """Eine Datei eines Datensatzes holen; Rückgabe: Bytes. Kaggle liefert sie als ZIP."""
    url = f"{API}/{dataset}"
    if file_name:
        url += f"?file_name={urllib.parse.quote(file_name)}"
    token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode("ascii")
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = response.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    if zipfile.is_zipfile(target):  # einzelne Dateien kommen trotzdem gepackt
        extract(target, target.parent / dataset.replace("/", "__"))
    return len(payload)


def write_tsv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        handle.write("\t".join(HEADER) + "\n")
        for row in rows:
            handle.write("\t".join(c.replace("\t", " ") for c in row) + "\n")
        temp = Path(handle.name)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="Landing-Verzeichnis (je Geschäftstag)")
    parser.add_argument("--datasets", required=True, help="seeds/kaggle.csv")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    now = datetime.now(UTC)
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
    target = Path(args.out) / f"kaggle_{now.strftime('%Y%m%dT%H%M%SZ')}__kaggle.tsv"

    auth = credentials()
    if auth is None:
        print(
            "fetch_kaggle: keine Zugangsdaten – KAGGLE_USERNAME/KAGGLE_KEY setzen oder "
            "~/.kaggle/kaggle.json anlegen (Kaggle → Account → Create New API Token). "
            "Der Lauf geht ohne diese Quelle weiter."
        )
        write_tsv(target, [])
        return 0

    rows: list[list[str]] = []
    with Path(args.datasets).open(encoding="utf-8", newline="") as handle:
        wanted = list(csv.DictReader(handle))
    for entry in wanted:
        dataset = (entry.get("dataset") or "").strip()
        if not dataset or (entry.get("enabled") or "").strip().lower() not in ("1", "true", "ja"):
            continue
        file_name = (entry.get("file") or "").strip()
        store = Path(args.out) / "kaggle" / f"{dataset.replace('/', '__')}.zip"
        try:
            size = download(dataset, file_name, auth, store, args.timeout)
        except (urllib.error.URLError, OSError, zipfile.BadZipFile) as exc:
            print(f"fetch_kaggle: {dataset} fällt aus ({type(exc).__name__})")
            continue
        print(f"fetch_kaggle: {dataset} → {size} Bytes")
        rows.append(
            [fetched_at, dataset, file_name or "(alles)", str(size), "", entry.get("note", "")]
        )

    write_tsv(target, rows)
    print(f"fetch_kaggle: {len(rows)} Datensätze")
    return 0


if __name__ == "__main__":
    sys.exit(main())
