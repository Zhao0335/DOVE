"""Evidence-based dimensional aggregation for DQC 0117.

All values come from the supplied filing.  The implementation deliberately keeps
candidate generation separate from selection: distinct financial-statement
networks can legitimately have different member sets for the same axis.
"""
from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


AXES = frozenset({
    "StatementClassOfStockAxis", "ProductOrServiceAxis",
    "PropertyPlantAndEquipmentByTypeAxis", "LongtermDebtTypeAxis",
    "RelatedPartyTransactionsByRelatedPartyAxis", "StatementBusinessSegmentsAxis",
    "FinancialInstrumentAxis", "LimitedPartnersCapitalAccountByClassAxis",
    "PartnerTypeOfPartnersCapitalAccountAxis",
    "FiniteLivedIntangibleAssetsByMajorClassAxis",
    "InformationByCategoryOfDebtSecurityAxis",
})
_XLINK = "{http://www.w3.org/1999/xlink}"
_XBRLDT = "{http://xbrl.org/2005/xbrldt}"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _local(name: Any) -> str:
    text = str(name or "")
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    if "#" in text:
        text = text.rsplit("#", 1)[-1]
    if ":" in text:
        return text.rsplit(":", 1)[-1]
    # SEC schema IDs are prefix_Concept, while ordinary member names may contain
    # underscores.  Only strip known namespace prefixes.
    for prefix in ("us-gaap_", "srt_", "dei_", "ifrs-full_"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _concept(obj: Any) -> str:
    return str(_get(obj, "concept", _get(obj, "name", "")))


def _context(parsed: Any, fact: Any) -> Any:
    contexts = _get(parsed, "contexts", {})
    return contexts.get(_get(fact, "context_id", _get(fact, "context", "")), {})


def _dims(ctx: Any) -> dict[str, str]:
    return {_local(a): _local(m) for a, m in (_get(ctx, "dimensions", {}) or {}).items()}


def _period(ctx: Any) -> tuple[Any, Any, Any]:
    return (_get(ctx, "start", _get(ctx, "start_date")),
            _get(ctx, "end", _get(ctx, "end_date")), _get(ctx, "instant"))


def _unit(parsed: Any, fact: Any) -> Any:
    unit_id = _get(fact, "unit_id", _get(fact, "unit"))
    unit = (_get(parsed, "units", {}) or {}).get(unit_id, unit_id)
    if isinstance(unit, Mapping):
        return tuple(sorted((str(k), repr(v)) for k, v in unit.items()))
    if isinstance(unit, (list, tuple)):
        return tuple(unit)
    return unit


def _value(fact: Any) -> Decimal | None:
    if _get(fact, "is_nil", False):
        return None
    value = _get(fact, "value")
    if value is None:
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _fallback_arcs(parsed: Any, kind: str) -> list[dict[str, Any]]:
    """Accept lxml roots as a fallback for integrations without arc objects."""
    arcs = []
    for root in (_get(parsed, "roots", {}) or {}).values():
        if isinstance(root, (list, tuple)):
            roots = root
        else:
            roots = [root]
        for current in roots:
            if not hasattr(current, "iter"):
                continue
            for link in current.iter():
                if _local(link.tag) != kind + "Link":
                    continue
                role = link.get(_XLINK + "role", "")
                locators = {loc.get(_XLINK + "label"): loc.get(_XLINK + "href", "")
                            for loc in link if _local(loc.tag) == "loc"}
                for arc in link:
                    if _local(arc.tag) != kind + "Arc" or arc.get("use") == "prohibited":
                        continue
                    arcs.append({"role": role,
                                 "parent": locators.get(arc.get(_XLINK + "from"), ""),
                                 "child": locators.get(arc.get(_XLINK + "to"), ""),
                                 "arcrole": arc.get(_XLINK + "arcrole", ""),
                                 "target_role": arc.get(_XBRLDT + "targetRole"),
                                 "usable": arc.get(_XBRLDT + "usable", "true") != "false",
                                 "order": arc.get("order", "0")})
    return arcs


def _arcs(parsed: Any, kind: str) -> list[dict[str, Any]]:
    result = []
    raw = _get(parsed, kind + "_arcs", None)
    if raw is None:
        raw = _fallback_arcs(parsed, kind)
    for arc in raw:
        if _get(arc, "use") == "prohibited":
            continue
        parent, child = _local(_get(arc, "parent")), _local(_get(arc, "child"))
        if not parent or not child:
            continue
        result.append({"role": str(_get(arc, "role", "")),
                       "parent": parent, "child": child,
                       "arcrole": str(_get(arc, "arcrole", "")).rsplit("/", 1)[-1],
                       "target_role": _get(arc, "target_role", _get(arc, "targetRole")),
                       "usable": _get(arc, "usable", True) not in (False, "false", "0"),
                       "order": _get(arc, "order", 0)})
    return result


def _role_score(role: str, definitions: Mapping[str, str]) -> float:
    label = (role + " " + str(definitions.get(role, ""))).lower()
    compact = "".join(c for c in label if c.isalnum())
    if any(s in compact for s in ("stockholder", "shareholder", "changesinequity", "equitystatement")):
        return -0.25
    if ("balancesheet" in compact or "financialposition" in compact or "financialcondition" in compact or
        ("statement" in compact and any(term in compact for term in
                                        ("income", "operation", "cashflow", "earnings")))):
        return 0.08
    if "disclosure" in compact or "detail" in compact:
        return -0.08
    return 0.0


def _statement_role(role: str, definitions: Mapping[str, str]) -> bool:
    description = str(definitions.get(role, ""))
    if description:
        return " - Statement - " in description
    return _role_score(role, definitions) > 0


def _member_tree(axis: str, role: str, definition: list[dict[str, Any]],
                 presentation: list[dict[str, Any]]) -> tuple[set[str], dict[str, set[str]], str]:
    """Return domain children and their ancestry, following definition targetRole."""
    by_parent: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for arc in definition:
        by_parent[(arc["role"], arc["parent"])].append(arc)
    domains = [arc for arc in by_parent[(role, axis)] if arc["arcrole"] == "dimension-domain"]
    children: dict[str, set[str]] = defaultdict(set)
    roots: set[str] = set()
    seen = set()
    queue: deque[tuple[str, str, bool]] = deque()
    for arc in domains:
        queue.append((str(arc["target_role"] or role), arc["child"], True))
    while queue:
        current_role, node, is_domain = queue.popleft()
        if (current_role, node) in seen:
            continue
        seen.add((current_role, node))
        for arc in by_parent[(current_role, node)]:
            if arc["arcrole"] != "domain-member":
                continue
            child = arc["child"]
            children[node].add(child)
            if is_domain:
                roots.add(child)
            queue.append((str(arc["target_role"] or current_role), child, False))
    if domains:
        return roots, children, "definition"

    # Some supplied documents omit definition dimensional relationships.  A
    # presentation subtree still gives a defensible, lower-confidence candidate.
    pchildren: dict[str, set[str]] = defaultdict(set)
    for arc in presentation:
        if arc["role"] == role:
            pchildren[arc["parent"]].add(arc["child"])
    domains_p = pchildren.get(axis, set())
    for domain in domains_p:
        if domain.endswith("Domain") or domain.endswith("Abstract"):
            roots.update(pchildren.get(domain, set()))
        else:
            roots.add(domain)
    return roots, pchildren, "presentation"


def _descendants(node: str, children: Mapping[str, set[str]]) -> set[str]:
    found, todo = set(), list(children.get(node, set()))
    while todo:
        child = todo.pop()
        if child in found:
            continue
        found.add(child)
        todo.extend(children.get(child, set()))
    return found


def _top_reported(roots: set[str], children: Mapping[str, set[str]], present: set[str],
                  descend_missing: bool) -> set[str]:
    selected, todo, visited = set(), list(roots), set()
    while todo:
        member = todo.pop()
        if member in visited:
            continue
        visited.add(member)
        if member in present:
            selected.add(member)
        elif descend_missing:
            todo.extend(children.get(member, set()))
    return selected


def generate_candidates(parsed: Any, target_fact: Any) -> list[dict[str, Any]]:
    """Generate dimension sums for a particular disclosed target fact.

    Candidates match entity, period, unit and every other dimension.  Reported
    ancestors suppress their descendants, so nested member values cannot be
    counted twice.  Default members represent the aggregate and are excluded.
    """
    target_context = _context(parsed, target_fact)
    target_dims = _dims(target_context)
    target_name = _local(_concept(target_fact))
    target_value = _value(target_fact)
    if target_value is None:
        return []
    target_unit = _unit(parsed, target_fact)
    target_entity = _get(target_context, "entity")
    target_typed = _get(target_context, "typed_dimensions", {}) or {}
    definition = _arcs(parsed, "definition")
    presentation = _arcs(parsed, "presentation")
    role_definitions = _get(parsed, "role_definitions", {}) or {}
    defaults = {arc["parent"]: arc["child"] for arc in definition
                if arc["arcrole"] == "dimension-default"}

    facts_by_axis: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for fact in _get(parsed, "facts", []):
        if _local(_concept(fact)) != target_name or _value(fact) is None or _unit(parsed, fact) != target_unit:
            continue
        context = _context(parsed, fact)
        if _period(context) != _period(target_context) or _get(context, "entity") != target_entity:
            continue
        if (_get(context, "typed_dimensions", {}) or {}) != target_typed:
            continue
        dims = _dims(context)
        for axis, member in dims.items():
            # 0117.10093 permits an unrestricted second axis below a subtotal
            # whose first axis is in the enumerated 0117 list.
            second_axis = bool(set(target_dims) & AXES) and axis != "LegalEntityAxis"
            if (axis not in AXES and not second_axis) or member == defaults.get(axis):
                continue
            # 0117 compares a dimension's default total with its member facts.
            # A target on that same axis is not such a total, unless explicitly
            # stated as its dimension default.
            if axis in target_dims and target_dims[axis] != defaults.get(axis):
                continue
            remaining = {k: v for k, v in dims.items() if k != axis and v != defaults.get(k)}
            target_remaining = {k: v for k, v in target_dims.items() if k != axis and v != defaults.get(k)}
            if remaining == target_remaining:
                facts_by_axis[axis][member].append(fact)

    role_nodes: dict[str, set[str]] = defaultdict(set)
    for arc in definition + presentation:
        role_nodes[arc["role"]].update((arc["parent"], arc["child"]))
    candidates = []
    for axis, member_facts in facts_by_axis.items():
        # Equivalent duplicate contexts should contribute once.  Keep the most
        # precise fact; conflicting values become independent candidate variants.
        options = {}
        conflicts = []
        for member, facts in member_facts.items():
            ranked = sorted(facts, key=lambda f: _precision(_get(f, "decimals")), reverse=True)
            options[member] = ranked[0]
            if len({_value(f) for f in ranked}) > 1:
                conflicts.append(member)
        applicable = [r for r, nodes in role_nodes.items() if axis in nodes]
        axis_candidate_count = len(candidates)
        for role in applicable:
            roots, children, source = _member_tree(axis, role, definition, presentation)
            if not roots:
                continue
            # Presence in the statement's line-item or presentation network is
            # much stronger evidence than an axis elsewhere in the filing.
            target_in_role = target_name in role_nodes[role]
            role_bonus = _role_score(role, role_definitions)
            for descend_missing in (False, True):
                selected = _top_reported(roots, children, set(options), descend_missing)
                selected.discard(defaults.get(axis))
                if not selected:
                    continue
                # Reject any overlap in roots introduced by repeated arcs.
                selected = {m for m in selected if not any(m in _descendants(a, children)
                                                          for a in selected if a != m)}
                used = [options[member] for member in sorted(selected)]
                value = sum((_value(fact) for fact in used), Decimal(0))
                complete = selected == roots
                score = (0.79 if source == "definition" else 0.57)
                score += role_bonus + (0.08 if target_in_role else -0.12)
                score += 0.025 if complete else -0.04
                # The published rule traverses all descendants and excludes a
                # fact only if an ancestor has a reported value.  Missing parent
                # members therefore require descent rather than omission.
                if not descend_missing and selected != _top_reported(roots, children, set(options), True):
                    score -= 0.15
                # A notes table does not authorize a high-confidence 0117 check.
                if not _statement_role(role, role_definitions):
                    score = min(score, 0.44)
                if any(m in conflicts for m in selected):
                    score -= 0.1
                candidates.append({"value": value, "score": min(0.98, max(0.1, score)),
                                   "method": "dimension_0117",
                                   "context_ids": [_get(f, "context_id") for f in used],
                                   "evidence": {"axis": axis, "role": role, "source": source,
                                                "members": sorted(selected),
                                                "root_members": sorted(roots),
                                                "complete": complete,
                                                "descend_missing": descend_missing,
                                                "target_in_role": target_in_role,
                                                "statement_role": _statement_role(role, role_definitions),
                                                "conflicts": conflicts,
                                                "facts": [{"context_id": _get(f, "context_id"),
                                                           "value": str(_value(f))} for f in used]}})
        known_children: dict[str, set[str]] = defaultdict(set)
        for arc in definition:
            if arc["arcrole"] == "domain-member":
                known_children[arc["parent"]].add(arc["child"])
        observed = {m for m in options if not any(m in _descendants(a, known_children)
                                                  for a in options if a != m)}
        # Supplied snippets can omit the member relationships used by a fact.
        # Keep the observed, non-overlapping member sum as a weaker alternative,
        # even when an unrelated or incomplete domain was supplied for the axis.
        observed_value = sum((_value(options[m]) for m in observed), Decimal(0))
        if not any(c["value"] == observed_value for c in candidates[axis_candidate_count:]):
            used = [options[m] for m in sorted(observed)]
            candidates.append({"value": sum((_value(f) for f in used), Decimal(0)),
                               "score": 0.38, "method": "dimension_unlinked",
                               "context_ids": [_get(f, "context_id") for f in used],
                               "evidence": {"axis": axis, "members": sorted(observed),
                                            "source": "observed_facts",
                                            "complete": False, "conflicts": conflicts,
                                            "facts": [{"context_id": _get(f, "context_id"),
                                                       "value": str(_value(f))} for f in used]}})
    # Different network paths frequently reproduce exactly the same evidence.
    unique = {}
    for candidate in candidates:
        evidence = candidate["evidence"]
        evidence["reported_equal"] = candidate["value"] == target_value
        # The task asks to reconstruct a flagged inconsistency.  A perfectly
        # reconciling breakdown does not explain that flag when an independent
        # breakdown gives a mismatch.  Retain it, but below comparable evidence.
        if evidence["reported_equal"]:
            candidate["score"] -= 0.25
        key = (candidate["value"], evidence["axis"], tuple(evidence["members"]))
        if key not in unique or candidate["score"] > unique[key]["score"]:
            unique[key] = candidate
    return sorted(unique.values(), key=lambda c: c["score"], reverse=True)


# Kept as a descriptive alias for callers that do not use candidate terminology.
solve_dimensions = generate_candidates


def _precision(decimals: Any) -> float:
    if str(decimals).upper() == "INF":
        return float("inf")
    try:
        return float(decimals)
    except (ValueError, TypeError):
        return -1000.0
