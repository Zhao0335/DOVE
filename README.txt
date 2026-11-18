DOVE supplementary archive v949, 30 October 2026

Public reproduction package for DOVE. See README.md for reviewer-response additions.
No external replication is claimed. Use existing Python 3.12.13 with
lxml 6.1.3, openpyxl 3.1.5 and et_xmlfile 2.0.0. No installer, model service
or network access is invoked. Use absolute paths and fresh output locations.

1. Reproduce submitted numerical pairs, from any working directory:
python -I /absolute/bundle/replay.py --input /absolute/test_inputs.jsonl --output /absolute/new_test_replay.jsonl --reference /absolute/accepted_predictions.jsonl
Input JSONL: id/query. Optional reference JSONL: id/extracted_value/calculated_value.
The runner checks MANIFEST.json and disables network connections. It reproduces
numerical pairs, not original file bytes. Canonical and evidence-gate optional
modes are post-submission prototypes, not the submitted policy.

2. Reproduce all matched Practice conditions without old caches:
python -I /absolute/bundle/scripts/paper_followup_v948.py --practice /absolute/public_dev.jsonl --out /absolute/new_practice_results
Practice JSONL requires id, query, dqc_id and answer with both numeric fields.
Only query reaches prediction functions. Labels are used for scoring.
This reruns four factorial cells and organizer/zero baselines, derives two
fixed-extraction controls, and writes protocol, outputs, cases and summary.

3. Inspect without rerunning:
evidence/summary.json: full, per-rule and stratified counts; overlap and contrast.
evidence/cases.jsonl: 3,320 scored records described by case.schema.json.
DATA_DICTIONARY.md: joins, field meanings, nulls, denominators and limits.
evidence/INDEX.json and MANIFEST.json: evidence and package hashes.

Expected correct pairs /332:
Frozen187; no aggregation182; no penalties183; neither182.
Organizer full25; extract0; negate32; zero0.
DOVE extraction copied0; DOVE extraction negated55.
All records remain in the primary denominator.

Benchmark queries and references must be supplied separately.
Practice: organizer-normalized TheFinAI/FinMR test split, 332 records.
Expected public_dev.jsonl SHA256:
5b55204b9fb2f8dae771b7fa20db8415aa30d74fa794a3e4b74c9b3a88de14d9
The local originating manifest has no upstream revision pin. Verify this hash
or document any new input version before interpreting numerical differences.
Test: organizer YanAdjeNole/FinReason-Task3 release, 680 queries.
Expected test_inputs.jsonl SHA256:
22301f5d087a0ebea90346faa3c12f83d97ce26d77880c32d3baa32078d4db70
Organizer starter kit prepares normalized public records:
https://github.com/The-FinAI/IEEE-bigdata-cup/tree/main/finreason_task3
No automatic download is implemented. Supply authorized local copies.

Original protocol records cached single-arm reuse. The packaged full rerun
is checked without that cache. Historical timings are not matched speed results.
Frozen solver objects are unchanged; the evaluator adds experiment metadata.
No queries, reference answers, credentials, hidden Test labels or competition
clients are bundled. Public Practice predictions/correctness flags are included.
See THIRD_PARTY_NOTICES.txt for ownership and generic-resource provenance.
