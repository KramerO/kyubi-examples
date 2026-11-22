# kyubi-examples

Beispielprojekte für [Kyubi-ETL](https://github.com/KramerO/KyubiETL) – reine Doku und Schulung, keine Test-Abhängigkeit des Kerns.

| Ordner | Inhalt |
|---|---|
| `basic` | Einstieg: Kundendaten bereinigen, Mappings, Pläne, Jobs, Dashboards; enthält die Kind-Sandbox `teams/vip` |
| `dwh_pilot` | DWH-Strecke Staging → Core → Mart |
| `genomics` | Genomdaten: Proteine und Sequenzen aus öffentlichen Quellen in einen Vault |
| `shared` | gemeinsame Kalender, Verbindungen und Datasets |
| `kyubi-workspace.yaml` | Workspace über alle Projekte (Segmente `shared`, `demo`, `dwh`, `bio`) |

## Benutzen

```bash
git clone https://github.com/KramerO/kyubi-examples.git
uv run kyubi validate kyubi-examples/basic              # im Kyubi-ETL-Repo
KYUBI_EXAMPLE_DATA=$PWD/kyubi-examples/basic/data \
  uv run kyubi run load_customers -C kyubi-examples/basic -RUN_DATE 2026-10-17
uv run kyubi hub -C kyubi-examples                      # alle Apps und Projekte
```

Kyubi-ETL selbst: Installation und Befehle stehen im Repo `KyubiETL` (`docs/DEVELOPMENT.md`,
`docs/architecture/20-repositories.md`).

## Daten Dritter

Die Beispieldaten in `basic` sind erfunden. Das Projekt `genomics` ruft öffentliche Daten (z. B. UniProt, NCBI, Kaggle)
**zur Laufzeit** über die Skripte in `genomics/scripts/` ab; diese Daten sind nicht Teil dieses Repositories und
unterliegen den Nutzungsbedingungen ihrer Anbieter.

## Lizenz

Apache License 2.0 – siehe [LICENSE](LICENSE) und [NOTICE](NOTICE).
