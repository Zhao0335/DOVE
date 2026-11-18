# DOVE

Difference-Oriented Verification and Execution for Financial Numerical Verification.

This release contains the frozen FinReason Cup 2026 Task 3 solver, offline experiments, and manuscript sources. Official Test performance is 655/680 (96.32%).

## Reproduction

Python 3.12 with the versions in requirements.txt was used. Install those packages in an existing suitable environment. Run from the repository root.

    python scripts/replay_controls.py --out /absolute/output/controls.json
    python scripts/paper_followup_v948.py --practice /absolute/public_dev.jsonl --out /absolute/output/practice
    python scripts/review_audit.py --test /absolute/test_inputs.jsonl --out /absolute/output/order.json
    python -I replay.py --input /absolute/test_inputs.jsonl --output /absolute/output/test.jsonl

All 120 synthetic controls and independent rational-number oracle operands are included in analysis/controlled_equations.json. Expected correct counts are 30, 30, 0, 30 for clean complete, corrupt complete, clean partial competitor, and corrupt partial competitor. No benchmark download is needed for this experiment.

Practice reproduction expects 187 full-system correct pairs, 182 without weak aggregation, 183 without discrepancy preference, and 182 without both. Baselines, input normalization and expected SHA256 hashes are documented in README.txt. Obtain benchmark inputs through the official participant hub at https://the-finai.github.io/IEEE-bigdata-cup/task3/ .

## Fact-order analysis

Seeds 17, 29 and 43 and reversal reproduce 16, 17, 15 and 23 changed Test pairs respectively. Their union contains 23 cases. Derived traces in analysis/order_cases.json identify 19 choices among eligible negative targets, two tied dimensional subtotals, and two unequal duplicate target values whose precision intervals overlap. The latter distinction was checked against original fact decimals. The generic audit script reports unequal values before this precision interpretation.

Canonical ordering changes 18 pairs relative to the accepted Test vector and yields 186/332 correct Practice pairs. These stability experiments have no per-case official Test correctness labels. Benchmark queries and reference values are supplied separately by the organizers.

## Evidence and scope

Practice outputs, 3,320 derived per-case records, schemas and factorial summaries are in evidence/. Parameter and canonical summaries are in analysis/. Sensitivity comparisons are retrospective evaluations on the Practice set used during development. This release supports author replay. Independent third-party replication remains to be established.

The frozen predictor is in runs/structured_test_v5/source/finreason. The replay runner verifies its manifest and blocks networking. Supplementary scripts make no model or competition requests.

## License

Original DOVE code uses the MIT license. Third-party files retain their own terms described in THIRD_PARTY_NOTICES.txt, vendor/official/finreason_task3/LICENSE and data/rules/dqc0015_manifest.json.
