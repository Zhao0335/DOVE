"""Generic DQC 0015 applicability and exact sign-reconstruction candidates.

The concept lists and member exclusions are public rule metadata, not filing
findings.  Applicability is determined from the fact's taxonomy namespace and
dimensional context; debit/credit balance alone is not an applicability test.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Mapping


RULES_DIR = Path(__file__).resolve().parents[2] / "data" / "rules"


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, Mapping) else getattr(obj, name, default)


def _local(name: Any) -> str:
    return str(name or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _namespace(name: str) -> str:
    return name[1:].split("}", 1)[0] if name.startswith("{") else name.split(":", 1)[0]


def _standard(name: str) -> bool:
    namespace = _namespace(name)
    return namespace in {"us-gaap", "srt"} or bool(re.search(r"(?:fasb\.org|xbrl\.us)/(?:us-gaap|srt)/", namespace))


def _taxonomy_year(parsed: Any, fact: Any) -> tuple[int | None, bool]:
    concept = str(_get(fact, "concept", _get(fact, "name", "")))
    namespace = _namespace(concept)
    family = _taxonomy_family(concept)
    match = re.search(r"/(?:us-gaap|srt|dei)/(20\d\d)", namespace)
    if match and family:
        return int(match.group(1)), True
    if namespace not in {"us-gaap", "srt", "dei"}:
        return None, False
    # QName-only callers can supply the namespace declaration through the query.
    years = re.findall(r"(?:fasb\.org|xbrl\.us|xbrl\.sec\.gov)/(?:us-gaap|srt|dei)/(20\d\d)", _get(parsed, "query", ""))
    return (int(years[0]), False) if years else (2026, False)


def _taxonomy_family(concept: str) -> str | None:
    namespace = _namespace(concept)
    if namespace in {"us-gaap", "srt", "dei"}:
        return namespace
    match = re.search(r"(?:fasb\.org|xbrl\.us)/(us-gaap|srt)/", namespace)
    if match:
        return match.group(1)
    if re.search(r"xbrl\.sec\.gov/dei/", namespace):
        return "dei"
    return None


@lru_cache(maxsize=40)
def concept_rules(year: int, family: str = "us-gaap") -> dict[str, str]:
    """Map a US GAAP concept to the published DQC rule suffix for that year."""
    if family == "us-gaap":
        path = RULES_DIR / f"dqc_15_usgaap_{year}_concepts.csv"
    elif family == "srt":
        path = RULES_DIR / (f"dqc_15_srt_{year}_concepts.csv" if year >= 2023 else "dqc_15_srt_concepts.csv")
    elif family == "dei":
        path = RULES_DIR / "dqc_15_dei_concepts.csv"
    else:
        return {}
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {_local(row[1].strip()): row[0].strip() for row in csv.reader(stream)
                if len(row) >= 2 and row[0].strip().isdigit() and row[1].strip()}


@lru_cache(maxsize=1)
def _exclusion_rules() -> dict[str, Any]:
    from openpyxl import load_workbook
    path = RULES_DIR / "DQC_0015_MemberExclusions.xlsx"
    workbook = load_workbook(path, read_only=True, data_only=True)
    strings: set[str] = set()
    members: set[str] = set()
    axes: set[str] = set()
    axis_members: set[tuple[str, str]] = set()
    try:
        for worksheet in workbook:
            for row in worksheet.iter_rows(min_row=2, values_only=True):
                dimensionality, test, element = row[3], str(row[4] or ""), row[7]
                if not element:
                    continue
                if dimensionality == "Member":
                    if "contains" in test.lower():
                        strings.add(str(element).lower())
                    elif "equals" in test.lower():
                        members.add(str(element))
                elif dimensionality == "Axis" and "equals" in test.lower():
                    if len(row) > 11 and row[11] and "member" in str(row[8] or "").lower():
                        axis_members.add((str(element), str(row[11])))
                    else:
                        axes.add(str(element))
    finally:
        workbook.close()
    # Explicit additions in the public 2026 constant-us-gaap.xule supplement
    # the older human-readable workbook.  Namespace migrations between US GAAP
    # and SRT do not alter these standard concepts' local names.
    strings.update({"basisswap", "unfunded"})
    members.add("EffectOfModifiedRetrospectiveApplicationAccountingStandardsUpdate201812Member")
    axes.update({"StatementBusinessSegmentsAxis", "InvestmentIdentifierAxis"})
    return {"strings": frozenset(strings), "members": frozenset(members),
            "axes": frozenset(axes), "axis_members": frozenset(axis_members)}


@lru_cache(maxsize=1)
def _provenance() -> dict[str, Any]:
    manifest = RULES_DIR / "dqc0015_manifest.json"
    if not manifest.exists():
        return {"repository": "https://github.com/DataQualityCommittee/dqc_us_rules"}
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    return {"repository": metadata["source_repository"], "revision": metadata["revision"],
            "manifest": str(manifest)}


def nonnegative_applicability(parsed: Any, fact: Any) -> dict[str, Any]:
    """Return applicability evidence for a fact, independently of its sign.

    Both positive and negative facts can be in the non-negative concept list;
    only a negative applicable fact is a DQC 0015 violation.
    """
    name = _local(_get(fact, "concept", _get(fact, "name", "")))
    year, exact_namespace = _taxonomy_year(parsed, fact)
    family = _taxonomy_family(str(_get(fact, "concept", _get(fact, "name", ""))))
    rules = concept_rules(year, family) if year is not None and family else {}
    suffix = rules.get(name)
    context = (_get(parsed, "contexts", {}) or {}).get(_get(fact, "context_id", ""), {})
    dimensions = _get(context, "dimensions", {}) or {}
    typed_dimensions = _get(context, "typed_dimensions", {}) or {}
    exclusions = []
    if dimensions or typed_dimensions:
        rule = _exclusion_rules()
        for axis, member in dimensions.items():
            axis_name, member_name = _local(axis), _local(member)
            member_lower = member_name.lower()
            for substring in sorted(rule["strings"]):
                if substring in member_lower:
                    exclusions.append({"kind": "member_substring", "axis": axis_name,
                                       "member": member_name, "substring": substring})
            if _standard(member) and member_name in rule["members"]:
                exclusions.append({"kind": "member", "axis": axis_name, "member": member_name})
            if _standard(axis) and axis_name in rule["axes"]:
                exclusions.append({"kind": "axis", "axis": axis_name})
            if (_standard(axis) and _standard(member) and
                    (axis_name, member_name) in rule["axis_members"]):
                exclusions.append({"kind": "axis_member", "axis": axis_name, "member": member_name})
            if axis_name == "IncomeTaxEffectChangeInLawAxis":
                exclusions.append({"kind": "extension_axis", "axis": axis_name})
        for axis in typed_dimensions:
            axis_name = _local(axis)
            if (_standard(axis) and axis_name in rule["axes"]) or axis_name == "IncomeTaxEffectChangeInLawAxis":
                exclusions.append({"kind": "typed_axis", "axis": axis_name})
    numeric_unit = _get(fact, "unit_id", _get(fact, "unit")) is not None
    eligible = bool(suffix and numeric_unit and not _get(fact, "is_nil", False) and not exclusions)
    return {"eligible": eligible, "listed_concept": bool(suffix), "concept": name,
            "taxonomy_year": year, "exact_namespace": exact_namespace,
            "taxonomy_family": family,
            "rule_id": f"DQC.US.0015.{suffix}" if suffix else None,
            "numeric_unit": numeric_unit, "exclusions": exclusions,
            "context_id": _get(fact, "context_id"), "source": _provenance()}


def is_nonnegative_fact(parsed: Any, fact: Any) -> bool:
    return nonnegative_applicability(parsed, fact)["eligible"]


def nonnegative_candidates(parsed: Any, target_fact: Any) -> list[dict[str, Any]]:
    """Return an exact absolute-value candidate only for a proven violation."""
    value = _get(target_fact, "value")
    try:
        value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return []
    if not value.is_finite() or value >= 0:
        return []
    evidence = nonnegative_applicability(parsed, target_fact)
    if not evidence["eligible"]:
        return []
    evidence["reported"] = str(value)
    return [{"value": abs(value), "score": 0.96 if evidence["exact_namespace"] else 0.90,
             "method": "nonnegative_rule_0015",
             "context_ids": [_get(target_fact, "context_id")], "evidence": evidence}]


def negative_target_facts(parsed: Any) -> list[dict[str, Any]]:
    """Locate eligible negative target facts in the exact requested period.

    This helper allows a caller to prefer an actual sign violation over a
    consolidated positive occurrence of the same concept.
    """
    target = _local(_get(parsed, "target", ""))
    start, endpoint = _get(parsed, "period_start"), _get(parsed, "period")
    result = []
    for fact in _get(parsed, "facts", []):
        if _local(_get(fact, "concept", _get(fact, "name", ""))) != target:
            continue
        context = (_get(parsed, "contexts", {}) or {}).get(_get(fact, "context_id", ""), {})
        if start and _get(context, "start") != start:
            continue
        end = _get(context, "instant") or _get(context, "end")
        if endpoint and ((len(str(endpoint)) == 4 and not str(end or "").startswith(str(endpoint))) or
                         (len(str(endpoint)) != 4 and end != endpoint)):
            continue
        candidates = nonnegative_candidates(parsed, fact)
        if candidates:
            result.append({"fact": fact, "candidate": candidates[0]})
    return result
