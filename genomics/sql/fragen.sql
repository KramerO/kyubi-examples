-- Fragen an den Genom-Strang (M26). Gegen PostgreSQL, Schemas gen_stage/gen_vault/gen_mart.
--   docker exec kyubi-test-postgres-1 psql -U kyubi -d kyubi_test -f fragen.sql

-- 1. Die Harmonisierung: Welche Organismen kennen **beide** Quellen?
--    Links die Proteine aus UniProt/SwissProt, rechts die Genome aus NCBI bzw. ENA –
--    verbunden allein über die Taxon-Nummer im HUB Organismus.
SELECT o.organism_id,
       s.organism_name,
       count(DISTINCT lp.hk_protein)  AS proteine_uniprot,
       count(DISTINCT ls.hk_sequence) AS genome_ncbi_ena
FROM gen_vault.hub_organism o
LEFT JOIN gen_vault.link_protein_organism  lp ON lp.hk_organism = o.hk_organism
LEFT JOIN gen_vault.link_sequence_organism ls ON ls.hk_organism = o.hk_organism
LEFT JOIN gen_vault.sat_organism s
       ON s.hk_organism = o.hk_organism AND s.tech_valid_to = TIMESTAMP '9999-12-31 00:00:00'
GROUP BY o.organism_id, s.organism_name
HAVING count(DISTINCT ls.hk_sequence) > 0
ORDER BY proteine_uniprot DESC;

-- 2. Die Genome: Länge, GC-Anteil, unbestimmte Basen.
--    Der GC-Anteil trennt die Erreger deutlich – Coronavirus 38 %, Ebola 41 %, Pest 48 %.
SELECT accession_base, source, bases, gc_percent, n_count, left(description, 60) AS beschreibung
FROM gen_mart.fact_sequence
ORDER BY bases DESC;

-- 3. Dasselbe Genom aus zwei Quellen: Stimmen die Prüfsummen überein?
--    Hier zeigt sich, ob ein zweiter Abzug (z. B. von Kaggle) wirklich dieselben Basen hat.
SELECT accession_base, count(*) AS quellen, count(DISTINCT sha256) AS verschiedene_pruefsummen,
       string_agg(DISTINCT source, ', ') AS quellen_namen
FROM gen_stage.stage_sequence
GROUP BY accession_base
HAVING count(*) > 1;

-- 4. Die größten Proteine je Erreger (SARS-CoV-2: pp1ab mit 7096 Aminosäuren).
SELECT s.organism_name, p.accession, p.entry_name, p.aa_length, left(p.protein_name, 50) AS protein
FROM gen_vault.sat_protein p
JOIN gen_vault.link_protein_organism lp ON lp.hk_protein = p.hk_protein
JOIN gen_vault.sat_organism s
  ON s.hk_organism = lp.hk_organism AND s.tech_valid_to = TIMESTAMP '9999-12-31 00:00:00'
WHERE p.tech_valid_to = TIMESTAMP '9999-12-31 00:00:00'
ORDER BY p.aa_length DESC
LIMIT 10;

-- 5. Gene mit mehreren Proteinen – beim Coronavirus ist das die Regel (ein Gen, viele
--    gespaltene Produkte), bei Bakterien die Ausnahme.
SELECT g.organism_id, g.gene_symbol, count(*) AS proteine
FROM gen_vault.link_protein_gene l
JOIN gen_vault.hub_gene g ON g.hk_gene = l.hk_gene
GROUP BY g.organism_id, g.gene_symbol
HAVING count(*) > 1
ORDER BY proteine DESC
LIMIT 10;
