# DOVE supplementary records, v948/v949

`protocol.json` records the input hash, source/resource hashes, conditions,
cached-output hashes and protocol timing. Historical machine paths document
original execution; reproduction uses command-line input/output paths.
`cases.jsonl` contains 3,320 rows: 332 Practice cases across ten conditions.
Its unique key is `(condition, id)`; `query_sha256` groups byte-identical query
strings independently of IDs. `summary.json` aggregates these records.
`INDEX.json` and `MANIFEST.json` bind evidence and package bytes respectively.
`case.schema.json` describes every field in the per-case file.

## Prediction and scoring

`prediction` contains strings or null. Null is unresolved and is not silently
changed to financial zero. `status` and `method` come from inference. The
organizer baseline's own zero fallback remains unchanged. The derived
`dove_extract` and `dove_negate` controls reuse DOVE's selected extraction,
retain nulls, and do not reselect facts.

The evaluator adds `rule`, correctness fields, `changed`, `gain` and `loss`
after prediction. Predictors receive query text only. Correctness normalizes
commas and Unicode minus using Decimal; both fields must match exactly.
No tolerance or LLM judge is used. Gain/loss is paired against frozen DOVE
for that ID. Changed concerns numeric pairs, not bytes or diagnostics.

## Diagnostic strata

`parser_recovery` and `target_present` are computed using the frozen DOVE
parser and reused for every condition, including organizer baselines.
They are not each baseline's own parse status. Recovery denotes logged XML
errors and does not certify original filing corruption. A missing target
does not establish that the financial quantity is absent.

`conflicting_reference` marks every record in an identical-query group with
multiple normalized reference pairs: 47 records in 17 groups. Their exclusion
is supplementary sensitivity analysis; the primary denominator remains 332.
No-recovery84 and recovery248 partition the sample. No-conflict285 overlaps both.

## Conditions

| Condition | Intervention |
|---|---|
| frozen | Original submitted four-module policy |
| no_unlinked | Remove only dimension_unlinked candidates |
| no_equality_penalties | Zero the 0.25 and 0.60 agreement penalties |
| no_both | Apply both previous interventions |
| official_full | Organizer baseline's unchanged full mode |
| official_extract | Organizer baseline's unchanged extract mode |
| official_negate | Organizer baseline's unchanged negate mode |
| zero | Constant zero for both fields |
| dove_extract | Copy DOVE's extracted value to the calculated field |
| dove_negate | Negate DOVE's extracted value for the calculated field |

The factorial count contrast is Y11-Y01-Y10+Y00; the first bit enables
unlinked aggregation and the second enables equality penalties. Its value is
187-182-183+182=4. It is a descriptive interaction in reused Practice cases,
not a significance test or estimate of future accuracy. The four penalty
losses overlap the five aggregation losses. The joint arm loses those five.

Organizer comparisons change parser, selection, arithmetic and ranking
together. Fixed-DOVE-extraction controls replace its calculated field;
they do not isolate each ranking heuristic. All analyses are retrospective.
Per-condition timings on reused arms are historical; they must not be used
as a controlled speed benchmark against the fresh baseline runs.
