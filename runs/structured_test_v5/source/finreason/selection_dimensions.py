"""Joint selection of an existing dimensional subtotal and its audit program.

This module consumes query evidence only.  No case identifiers or answer labels
participate in selection.  It is intentionally independent of solver policy.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from .dimensions import AXES, generate_candidates


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _local(value: Any) -> str:
    return str(value or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _dims(context: Any) -> dict[str, str]:
    return {_local(a): _local(m) for a, m in (_get(context, "dimensions", {}) or {}).items()}


def _period(context: Any) -> tuple[Any, Any, Any]:
    return _get(context, "start"), _get(context, "end"), _get(context, "instant")


def _unit(parsed: Any, fact: Any) -> Any:
    unit_id = _get(fact, "unit_id")
    return (_get(parsed, "units", {}) or {}).get(unit_id, unit_id)


def joint_target_candidates(parsed: Any) -> list[dict[str, Any]]:
    """Enumerate real qualified subtotals with a supported second-axis sum.

    Each entry contains ``fact``, its executable ``candidate``, an independent
    selection ``score``, and contextual evidence.  The fixed dimensions of the
    subtotal must be preserved exactly in every operand; each operand adds only
    the proposed aggregation axis.  Reconciling sums are excluded because they
    do not explain the task's reported inconsistency.

    Network membership is checked jointly: a role that contains the target,
    every fixed axis/member, and the new axis provides stronger evidence than
    merely finding similarly named dimensional facts elsewhere in the filing.

    DQC 0117.10093 starts from component facts with exactly two dimensions;
    the existing subtotal retains their first dimension.  Facts with more than
    two dimensions are used by CheckSubTotalExists to validate a subtotal, not
    as direct three-dimensional addends for a two-dimensional target.  This
    selector therefore promotes only a one-dimensional existing subtotal.
    Generic source: dqc_us_rules/source/us/2026/DQC_0117.xule, assertion
    US.0117.10093 (public DQC repository revision 64abefd357c34ef45f9f4336a1e3171984d76601).
    """
    contexts = _get(parsed, "contexts", {}) or {}
    target_name = _local(_get(parsed, "target"))
    endpoint = _get(parsed, "period")
    start = _get(parsed, "period_start")
    facts = _get(parsed, "facts", []) or []
    facts_by_context: dict[str, list[Any]] = defaultdict(list)
    for fact in facts:
        if _local(_get(fact, "concept", _get(fact, "name"))) == target_name:
            facts_by_context[_get(fact, "context_id")].append(fact)
    role_nodes: dict[str, set[str]] = defaultdict(set)
    for kind in ("definition_arcs", "presentation_arcs"):
        for arc in _get(parsed, kind, []) or []:
            if _get(arc, "use") == "prohibited":
                continue
            role_nodes[str(_get(arc, "role", ""))].update(
                (_local(_get(arc, "parent")), _local(_get(arc, "child"))))
    results = []
    seen = set()
    for fact in facts:
        if (_local(_get(fact, "concept", _get(fact, "name"))) != target_name or
                _get(fact, "value") is None or _get(fact, "is_nil", False)):
            continue
        context_id = _get(fact, "context_id")
        context = contexts.get(context_id)
        fixed = _dims(context)
        if len(fixed) != 1 or not (set(fixed) & AXES) or _get(context, "typed_dimensions", {}):
            continue
        if endpoint and (_get(context, "instant") or _get(context, "end")) != endpoint:
            continue
        if start and _get(context, "start") != start:
            continue
        for candidate in generate_candidates(parsed, fact):
            if candidate["value"] == _get(fact, "value") or candidate.get("score", 0) < .3:
                continue
            aggregate_axis = candidate.get("evidence", {}).get("axis")
            if not aggregate_axis or aggregate_axis in fixed or aggregate_axis == "LegalEntityAxis":
                continue
            operand_contexts = []
            for operand_context_id in candidate.get("context_ids", []):
                operand_context = contexts.get(operand_context_id)
                operand_dims = _dims(operand_context)
                if aggregate_axis not in operand_dims:
                    continue
                if {a: m for a, m in operand_dims.items() if a != aggregate_axis} != fixed:
                    continue
                if (_period(operand_context) != _period(context) or
                        _get(operand_context, "entity") != _get(context, "entity")):
                    continue
                matching = [f for f in facts_by_context[operand_context_id]
                            if _unit(parsed, f) == _unit(parsed, fact) and _get(f, "value") is not None]
                if matching:
                    operand_contexts.append(operand_context_id)
            if not operand_contexts:
                continue
            required_nodes = {target_name, aggregate_axis, *fixed, *fixed.values()}
            compatible_roles = [role for role, nodes in role_nodes.items() if required_nodes <= nodes]
            advertised_role = candidate.get("evidence", {}).get("role")
            network_supported = bool(compatible_roles)
            # Source order breaks ties only after contextual and role evidence.
            # A context compatible with the supplied table outranks an unrelated
            # breakdown that appears earlier in the complete instance document.
            score = .96 if network_supported else .78
            if len(fixed) == 1:
                score += .02
            score += min(candidate.get("score", 0), .99) * .01
            key = (context_id, candidate["value"], aggregate_axis,
                   tuple(sorted(operand_contexts)))
            if key in seen:
                continue
            seen.add(key)
            results.append({"fact": fact, "candidate": candidate, "score": score,
                            "evidence": {"selection_method": "existing_dimensional_subtotal",
                                         "fixed_dimensions": fixed,
                                         "aggregate_axis": aggregate_axis,
                                         "operand_contexts": operand_contexts,
                                         "compatible_roles": compatible_roles,
                                         "advertised_role": advertised_role,
                                         "network_supported": network_supported,
                                         "fixed_dimension_count": len(fixed),
                                         "source_index": _get(fact, "source_index", 0)}})
    return sorted(results, key=lambda r: (-r["score"], _get(r["fact"], "source_index", 0)))
