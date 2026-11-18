"""Executable XBRL evidence reconstruction for FinReason Task 3.

Only the supplied query is consumed. Numeric operations use Decimal throughout.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import importlib
import re
from typing import Any

from lxml import etree

XLINK = "http://www.w3.org/1999/xlink"
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XML_PARSER = dict(resolve_entities=False, no_network=True, huge_tree=True)


def local_name(name: str | None) -> str:
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1] if isinstance(name, str) else ""


def expand_qname(name: str, element: etree._Element) -> str:
    if name.startswith("{"):
        return name
    if ":" in name:
        prefix, suffix = name.split(":", 1)
        uri = element.nsmap.get(prefix)
        return f"{{{uri}}}{suffix}" if uri else name
    uri = element.nsmap.get(None)
    return f"{{{uri}}}{name}" if uri else name


def decimal_value(value: str | None) -> Decimal | None:
    if value is None:
        return None
    value = value.strip().replace(",", "").replace("\u2212", "-")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    try:
        number = Decimal(value)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


@dataclass
class Context:
    id: str
    entity: str = ""
    start: str | None = None
    end: str | None = None
    instant: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)
    typed_dimensions: dict[str, str] = field(default_factory=dict)

    @property
    def period(self) -> tuple[str | None, str | None, str | None]:
        return self.start, self.end, self.instant

    @property
    def dimension_key(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted({**self.dimensions, **self.typed_dimensions}.items()))

    @property
    def key(self) -> tuple[Any, ...]:
        return self.entity, self.period, self.dimension_key


@dataclass
class Fact:
    concept: str
    name: str
    value: Decimal | None
    raw_value: str
    context_id: str
    unit_id: str | None = None
    decimals: str | None = None
    is_nil: bool = False
    source_index: int = 0

    @property
    def local_name(self) -> str:
        return self.name


@dataclass
class ParsedQuery:
    query: str
    roots: dict[str, etree._Element] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)
    contexts: dict[str, Context] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    calculation_arcs: list[dict[str, Any]] = field(default_factory=list)
    definition_arcs: list[dict[str, Any]] = field(default_factory=list)
    presentation_arcs: list[dict[str, Any]] = field(default_factory=list)
    role_definitions: dict[str, str] = field(default_factory=dict)
    concepts: dict[str, dict[str, str]] = field(default_factory=dict)
    target: str | None = None
    period: str | None = None
    period_start: str | None = None
    target_qname: str | None = None
    question: str = ""
    errors: list[str] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


def _xml_documents(query: str):
    # A query contains several standalone XML documents plus plain taxonomy text.
    pattern = re.compile(r"<(?P<qname>(?:[A-Za-z_][\w.-]*:)?(?:xbrl|linkbase|schema))(?=[\s>])")
    cursor = 0
    for match in pattern.finditer(query):
        if match.start() < cursor:
            continue
        qname = match.group("qname")
        opening_end = query.find(">", match.end())
        if opening_end >= 0 and query[opening_end - 1] == "/":
            yield qname, query[match.start():opening_end + 1], match.start(), opening_end + 1
            cursor = opening_end + 1
            continue
        closing = re.search(r"</" + re.escape(qname) + r"\s*>", query[match.end():])
        if closing is None:
            # The public FinMR practice release contains document excerpts that
            # end halfway through an element. Recover the supplied prefix only.
            boundary = re.search(r"\n\s*##", query[match.end():])
            end = match.end() + boundary.start() if boundary else len(query)
        else:
            end = match.end() + closing.end()
        yield qname, query[match.start():end], match.start(), end
        cursor = end


def _href_concept(href: str) -> str:
    fragment = href.rsplit("#", 1)[-1]
    # Taxonomy schema identifiers conventionally use us-gaap_Concept.
    return fragment.split("_", 1)[-1] if "_" in fragment else local_name(fragment)


def _read_networks(parsed: ParsedQuery, root: etree._Element) -> None:
    for role in root.iter():
        if local_name(role.tag) == "roleType":
            definition = next(("".join(x.itertext()).strip() for x in role if local_name(x.tag) == "definition"), "")
            parsed.role_definitions[role.get("roleURI", "")] = definition
    collections = {"calculationLink": ("calculationArc", parsed.calculation_arcs),
                   "definitionLink": ("definitionArc", parsed.definition_arcs),
                   "presentationLink": ("presentationArc", parsed.presentation_arcs)}
    for link in root.iter():
        kind = local_name(link.tag)
        if kind not in collections:
            continue
        arc_name, output = collections[kind]
        role = link.get(f"{{{XLINK}}}role", "")
        locators = {x.get(f"{{{XLINK}}}label"): _href_concept(x.get(f"{{{XLINK}}}href", ""))
                    for x in link if local_name(x.tag) == "loc"}
        for arc in link:
            if local_name(arc.tag) != arc_name:
                continue
            source, dest = arc.get(f"{{{XLINK}}}from"), arc.get(f"{{{XLINK}}}to")
            if source not in locators or dest not in locators:
                continue
            output.append({"role": role, "parent": locators[source], "child": locators[dest],
                           "weight": decimal_value(arc.get("weight")) or Decimal(1),
                           "order": decimal_value(arc.get("order")) or Decimal(0),
                           "arcrole": arc.get(f"{{{XLINK}}}arcrole", ""),
                           "target_role": arc.get("{http://xbrl.org/2005/xbrldt}targetRole"),
                           "usable": arc.get("{http://xbrl.org/2005/xbrldt}usable", "true") != "false",
                           "use": arc.get("use", "optional"), "priority": int(arc.get("priority", "0"))})


def _read_instance(parsed: ParsedQuery, root: etree._Element) -> None:
    for element in root:
        name = local_name(element.tag)
        if name == "context":
            context = Context(id=element.get("id", ""))
            for child in element.iter():
                child_name = local_name(child.tag)
                if child_name == "identifier":
                    context.entity = child.text or ""
                elif child_name == "startDate":
                    context.start = child.text
                elif child_name == "endDate":
                    context.end = child.text
                elif child_name == "instant":
                    context.instant = child.text
                elif child_name == "explicitMember":
                    context.dimensions[expand_qname(child.get("dimension", ""), child)] = expand_qname((child.text or "").strip(), child)
                elif child_name == "typedMember":
                    context.typed_dimensions[expand_qname(child.get("dimension", ""), child)] = etree.tostring(child, encoding="unicode")
            parsed.contexts[context.id] = context
        elif name == "unit":
            parsed.units[element.get("id", "")] = "|".join(
                expand_qname((x.text or "").strip(), x) for x in element.iter() if local_name(x.tag) == "measure")
        elif element.get("contextRef"):
            raw = "".join(element.itertext()).strip()
            nil = element.get(f"{{{XSI}}}nil") in {"true", "1"}
            number = None if nil else decimal_value(raw)
            if number is not None and element.get("sign") == "-" and not raw.startswith("-"):
                number = -number
            if number is not None and element.get("scale"):
                number *= Decimal(10) ** int(element.get("scale"))
            parsed.facts.append(Fact(concept=element.tag, name=name, value=number,
                                     raw_value=raw, context_id=element.get("contextRef", ""),
                                     unit_id=element.get("unitRef"), decimals=element.get("decimals"),
                                     is_nil=nil, source_index=len(parsed.facts)))


def _identify_question(parsed: ParsedQuery, last_xml_end: int) -> None:
    tail = parsed.query[last_xml_end:]
    exact = re.search(r"Question1:.*?reported value of\s+([\w.-]+:[\w.-]+).*?period\s+(\d{4}-\d{2}-\d{2})(?:\s+to\s+(\d{4}-\d{2}-\d{2}))?", tail, re.S)
    if exact:
        parsed.question = tail[exact.start():].strip()
        parsed.target_qname = exact.group(1)
        parsed.target = local_name(exact.group(1))
        parsed.period_start = exact.group(2) if exact.group(3) else None
        parsed.period = exact.group(3) or exact.group(2)
        return
    # Prefer an explicitly marked question; taxonomy declarations may follow XML.
    marks = list(re.finditer(r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:Question|Target|Task)\s*[:\n]", tail, re.I))
    parsed.question = tail[marks[-1].end():].strip() if marks else tail[-4000:].strip()
    candidates = {f.name for f in parsed.facts}
    known = sorted(candidates, key=len, reverse=True)
    matches = [(m.start(), name) for name in known
               for m in re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", parsed.question)]
    if matches:
        # The target almost always appears in the first sentence of the question.
        parsed.target = min(matches)[1]
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", parsed.question)
    if dates:
        parsed.period = dates[-1]
    elif match := re.search(r"\b((?:19|20)\d{2})\b", parsed.question):
        parsed.period = match.group(1)


def parse_query(query: str) -> ParsedQuery:
    parsed = ParsedQuery(query=query)
    last_xml_end = 0
    for index, (qname, text, _start, end) in enumerate(_xml_documents(query)):
        last_xml_end = max(last_xml_end, end)
        try:
            root = etree.fromstring(text.encode("utf-8"), etree.XMLParser(**XML_PARSER))
        except etree.XMLSyntaxError as exc:
            parsed.errors.append(f"xml_{index}: {exc.msg}")
            try:
                root = etree.fromstring(text.encode("utf-8"), etree.XMLParser(recover=True, **XML_PARSER))
            except etree.XMLSyntaxError:
                continue
        if root is None:
            continue
        parsed.roots[f"{local_name(qname)}_{index}"] = root
        if local_name(root.tag) == "xbrl":
            _read_instance(parsed, root)
        _read_networks(parsed, root)
        if local_name(root.tag) == "schema":
            for item in root.iter():
                if local_name(item.tag) == "element" and item.get("name"):
                    parsed.concepts[item.get("name")] = dict(item.attrib)
    _identify_question(parsed, last_xml_end)
    return parsed


def _period_score(context: Context, period: str | None) -> float:
    if not period:
        return 0.0
    endpoint = context.instant or context.end or ""
    if endpoint == period:
        return 5.0
    if len(period) == 4 and endpoint.startswith(period):
        return 4.0
    return -8.0


def target_facts(parsed: ParsedQuery) -> list[Fact]:
    facts = [f for f in parsed.facts if f.name == parsed.target and f.value is not None]
    def score(fact: Fact):
        context = parsed.contexts.get(fact.context_id, Context(fact.context_id))
        start_score = 0 if parsed.period_start is None else (5 if context.start == parsed.period_start else -10)
        return (_period_score(context, parsed.period) + start_score, -len(context.dimensions) - len(context.typed_dimensions),
                context.end or context.instant or "", -(context.start or "").count("x"), -fact.source_index)
    return sorted(facts, key=score, reverse=True)


def _equivalent_facts(parsed: ParsedQuery, fact: Fact) -> dict[str, list[Fact]]:
    target_context = parsed.contexts.get(fact.context_id)
    output: dict[str, list[Fact]] = defaultdict(list)
    target_unit = parsed.units.get(fact.unit_id or "", fact.unit_id)
    for child in parsed.facts:
        if child.value is None:
            continue
        context = parsed.contexts.get(child.context_id)
        same_context = child.context_id == fact.context_id or (context and target_context and context.key == target_context.key)
        same_unit = parsed.units.get(child.unit_id or "", child.unit_id) == target_unit
        if same_context and same_unit:
            output[child.name].append(child)
    # DQC 0126 selects duplicate occurrences at the financial statements'
    # modal precision, then a more precise occurrence, then a coarser one.
    statement_roles = {role for role, text in parsed.role_definitions.items()
                       if re.search(r"statement|balance.?sheet|income.?statement|cash.?flow", text, re.I)
                       and not re.search(r"disclosure", text, re.I)}
    statement_concepts = {arc[key] for arc in parsed.presentation_arcs
                          if arc["role"] in statement_roles for key in ("parent", "child")}
    precision_facts = [f for values in output.values() for f in values
                       if f.decimals is not None and (not statement_concepts or f.name in statement_concepts)]
    counts = Counter(f.decimals for f in precision_facts)
    if counts:
        modal = counts.most_common(1)[0][0]
        modal_numeric = Decimal(modal) if modal.upper() != "INF" else Decimal("Infinity")
        def precision_rank(item: Fact):
            if item.decimals is None:
                return 3, item.source_index
            numeric = Decimal(item.decimals) if item.decimals.upper() != "INF" else Decimal("Infinity")
            return (0 if numeric == modal_numeric else 1 if numeric >= modal_numeric else 2), item.source_index
        for values in output.values():
            values.sort(key=precision_rank)
    return output


def calculation_candidates(parsed: ParsedQuery, fact: Fact) -> list[dict[str, Any]]:
    networks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for arc in parsed.calculation_arcs:
        if arc["parent"] == fact.name:
            networks[arc["role"]].append(arc)
    facts = _equivalent_facts(parsed, fact)
    candidates = []
    for role, arcs in networks.items():
        # Effective relationships: handle duplicates and prohibited overrides.
        effective: dict[str, dict] = {}
        for arc in arcs:
            previous = effective.get(arc["child"])
            if previous is None or arc["priority"] >= previous["priority"]:
                effective[arc["child"]] = arc
        arcs = [a for a in effective.values() if a["use"] != "prohibited"]
        present = [a for a in arcs if a["child"] in facts]
        missing = [a["child"] for a in arcs if a["child"] not in facts]
        if not present:
            continue
        total = Decimal(0)
        operands = []
        conflicts = []
        for arc in present:
            matching = facts[arc["child"]]
            values = {f.value for f in matching}
            if len(values) > 1:
                conflicts.append(arc["child"])
            selected = matching[0]
            total += arc["weight"] * selected.value
            operands.append({"concept": selected.name, "value": format_decimal(selected.value),
                             "weight": format_decimal(arc["weight"]), "context_id": selected.context_id})
        description = parsed.role_definitions.get(role, "")
        statement_bonus = 0.02 if re.search(r"statement|balance.?sheet|income|cash.?flow", description, re.I) else 0
        coverage = len(present) / max(len(arcs), 1)
        score = 0.76 + 0.13 * coverage + min(len(present), 10) * 0.004 + statement_bonus - 0.05 * bool(conflicts)
        candidates.append({"value": total, "score": score, "method": "calculation_network", "role": role,
                           "evidence": {"operands": operands, "missing": missing, "coverage": coverage,
                                        "role_definition": description, "duplicate_conflicts": conflicts}})
    return sorted(candidates, key=lambda c: (c["score"], len(c["evidence"]["operands"])), reverse=True)


def inverse_calculation_candidates(parsed: ParsedQuery, fact: Fact) -> list[dict[str, Any]]:
    """Solve every supplied weighted equation that contains the target as a child."""
    networks: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for arc in parsed.calculation_arcs:
        networks[(arc["role"], arc["parent"])].append(arc)
    facts = _equivalent_facts(parsed, fact)
    output = []
    for (role, parent), raw_arcs in networks.items():
        if not any(a["child"] == fact.name for a in raw_arcs) or parent not in facts:
            continue
        effective = {}
        for arc in raw_arcs:
            prior = effective.get(arc["child"])
            if prior is None or arc["priority"] >= prior["priority"]:
                effective[arc["child"]] = arc
        arcs = [a for a in effective.values() if a["use"] != "prohibited"]
        target_arc = next((a for a in arcs if a["child"] == fact.name), None)
        if target_arc is None or not target_arc["weight"]:
            continue
        siblings = [a for a in arcs if a["child"] != fact.name]
        present = [a for a in siblings if a["child"] in facts]
        missing = [a["child"] for a in siblings if a["child"] not in facts]
        total = facts[parent][0].value
        operands = [{"concept": parent, "value": format_decimal(total), "coefficient": "1"}]
        for arc in present:
            child = facts[arc["child"]][0]
            total -= arc["weight"] * child.value
            operands.append({"concept": child.name, "value": format_decimal(child.value), "coefficient": format_decimal(-arc["weight"])})
        total /= target_arc["weight"]
        coverage = (len(present) + 1) / (len(siblings) + 1)
        output.append({"value": total, "score": 0.72 + 0.16 * coverage + min(len(present), 10) * .002,
                       "method": "inverse_calculation", "role": role,
                       "evidence": {"parent": parent, "operands": operands, "missing": missing,
                                    "target_weight": format_decimal(target_arc["weight"]), "coverage": coverage}})
    return output


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format_decimal(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _all_candidates(parsed: ParsedQuery, target: Fact) -> list[dict[str, Any]]:
    candidates = calculation_candidates(parsed, target)
    inverse = inverse_calculation_candidates(parsed, target)
    if candidates:
        # DQC 0126 reconstructs a parent's supplied direct addends. Reverse
        # equations remain useful diagnostics but cannot replace that operation.
        for candidate in inverse:
            candidate["evidence"]["forward_available_inverse_fallback_only"] = True
            candidate["evidence"]["original_score"] = candidate["score"]
            candidate["score"] -= 1.0
    candidates.extend(inverse)
    try:
        dimension_module = importlib.import_module("finreason.dimensions")
        candidates.extend(dimension_module.generate_candidates(parsed, target))
    except ImportError:
        pass
    try:
        nonnegative_module = importlib.import_module("finreason.nonnegative")
        candidates.extend(nonnegative_module.nonnegative_candidates(parsed, target))
    except ImportError:
        pass
    for candidate in candidates:
        if candidate["value"] == target.value:
            candidate["score"] -= .60
            candidate.setdefault("evidence", {})["reported_equal_penalty"] = .60
    candidates.append({"value": target.value, "score": 0.10, "method": "reported_fallback", "evidence": {}})
    return sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)


def _same_question_period(parsed: ParsedQuery, fact: Fact) -> bool:
    context = parsed.contexts.get(fact.context_id)
    return bool(context and (context.instant or context.end) == parsed.period and
                (parsed.period_start is None or context.start == parsed.period_start))


def select_nonnegative_fact(parsed: ParsedQuery, negatives: list[Fact]) -> tuple[Fact, dict[str, Any]]:
    """Prefer equal-value support in strictly nested semantic contexts.

    Repeated facts and differently named equivalent contexts are one evidence
    item. A support pair must preserve concept, entity, complete period, unit,
    and every existing dimension/member; one context adds at least one axis.
    Document order breaks ties between supported values and remains the default
    when there is no such pair. This is a context selection policy, not a claim
    that the nonnegative rule itself identifies only one offending fact.
    """
    ordered = sorted(negatives, key=lambda fact: fact.source_index)
    canonical = []
    seen = set()
    for fact in ordered:
        context = parsed.contexts[fact.context_id]
        unit = parsed.units.get(fact.unit_id or "", fact.unit_id)
        key = (fact.concept, context.key, unit, fact.value)
        if key in seen:
            continue
        seen.add(key)
        canonical.append((fact, context, unit))
    supported_values = set()
    pairs = []
    for index, (left, left_context, left_unit) in enumerate(canonical):
        for right, right_context, right_unit in canonical[index + 1:]:
            if (left.value != right.value or left.concept != right.concept or
                    left_context.entity != right_context.entity or
                    left_context.period != right_context.period or left_unit != right_unit):
                continue
            left_dims = dict(left_context.dimension_key)
            right_dims = dict(right_context.dimension_key)
            if len(left_dims) == len(right_dims):
                continue
            if len(left_dims) < len(right_dims):
                coarse, fine = (left, left_context, left_dims), (right, right_context, right_dims)
            else:
                coarse, fine = (right, right_context, right_dims), (left, left_context, left_dims)
            if not all(fine[2].get(axis) == member for axis, member in coarse[2].items()):
                continue
            supported_values.add(left.value)
            pairs.append({"value": format_decimal(left.value), "coarse_context": coarse[0].context_id,
                          "fine_context": fine[0].context_id, "coarse_dimensions": coarse[2],
                          "fine_dimensions": fine[2]})
    selected = next((fact for fact in ordered if fact.value in supported_values), ordered[0])
    evidence = {
        "reason": ("qualified_nonnegative_fact_with_nested_context_support" if supported_values
                   else "qualified_nonnegative_fact_in_document_order"),
        "source_first_context": ordered[0].context_id,
        "eligible_contexts": list(dict.fromkeys(fact.context_id for fact in ordered)),
        "semantic_candidate_contexts": len(canonical),
        "duplicate_evidence_items_removed": len(ordered) - len(canonical),
        "nested_support_values": [format_decimal(value) for value in sorted(supported_values)],
        "nested_support_pairs": pairs,
    }
    return selected, evidence


def solve(query: str) -> dict[str, Any]:
    parsed = parse_query(query)
    targets = target_facts(parsed)
    diagnostics: dict[str, Any] = {"target": parsed.target, "period": parsed.period,
                                   "facts": len(parsed.facts), "contexts": len(parsed.contexts),
                                   "documents": len(parsed.roots), "parse_errors": parsed.errors}
    if not targets:
        return {"extracted_value": None, "calculated_value": None, "status": "target_not_found", "diagnostics": diagnostics}
    target = targets[0]
    try:
        nonnegative_module = importlib.import_module("finreason.nonnegative")
        violation_ids = {id(item["fact"]) for item in nonnegative_module.negative_target_facts(parsed)}
        negatives = [f for f in targets if id(f) in violation_ids]
    except ImportError:
        negatives = []
    if negatives:
        initial_context = target.context_id
        target, sign_selection = select_nonnegative_fact(parsed, negatives)
        sign_selection["initial_context"] = initial_context
        diagnostics["context_selection"] = sign_selection
    joint_dimension = None
    if not negatives:
        try:
            selection_module = importlib.import_module("finreason.selection_dimensions")
            joint_candidates = selection_module.joint_target_candidates(parsed)
        except ImportError:
            joint_candidates = []
        if joint_candidates:
            joint_dimension = joint_candidates[0]
            initial_context = target.context_id
            target = joint_dimension["fact"]
            diagnostics["context_selection"] = {
                "reason": "joint_dimensional_subtotal_and_aggregation",
                "initial_context": initial_context,
                "selection_score": joint_dimension["score"],
                "evidence": _json_safe(joint_dimension["evidence"]),
                "candidate_contexts": list(dict.fromkeys(item["fact"].context_id for item in joint_candidates)),
            }
    exact_targets = [f for f in targets if _same_question_period(parsed, f)]
    # The output contract assigns zero to an absent reported aggregate. A
    # virtual context lets supplied dimensional components still reconstruct it.
    if exact_targets and not negatives and joint_dimension is None and all(parsed.contexts[f.context_id].dimension_key for f in exact_targets):
        original_context = parsed.contexts[target.context_id]
        virtual_id = "__finreason_virtual_aggregate__"
        virtual_context = Context(virtual_id, entity=original_context.entity, start=original_context.start,
                                  end=original_context.end, instant=original_context.instant)
        parsed.contexts[virtual_id] = virtual_context
        virtual_fact = Fact(target.concept, target.name, Decimal(0), "0", virtual_id, target.unit_id, target.decimals, source_index=-1)
        virtual_candidates = _all_candidates(parsed, virtual_fact)
        if any(c["method"].startswith("dimension") and c["score"] >= .3 for c in virtual_candidates):
            target = virtual_fact
            diagnostics["virtual_aggregate"] = {"reported_absent": True, "basis_contexts": [f.context_id for f in exact_targets],
                                                "reason": "no_unqualified_fact_and_no_applicable_sign_violation"}
    try:
        candidates = _all_candidates(parsed, target)
    except Exception as exc:
        diagnostics["dimension_error"] = f"{type(exc).__name__}: {exc}"
        candidates = calculation_candidates(parsed, target) + inverse_calculation_candidates(parsed, target)
        candidates.append({"value": target.value, "score": .1, "method": "reported_fallback", "evidence": {}})
        candidates.sort(key=lambda c: c["score"], reverse=True)
    if joint_dimension is not None:
        selected = dict(joint_dimension["candidate"])
        selected["evidence"] = dict(selected.get("evidence", {}))
        selected["evidence"]["generation_score"] = selected["score"]
        selected["evidence"]["joint_target_selection"] = _json_safe(joint_dimension["evidence"])
        selected["score"] = joint_dimension["score"]
        # Context and its aggregation program were selected together above.
        # Keep alternative arithmetic visible while executing that program.
        candidates = [selected] + [candidate for candidate in candidates
                                   if not (candidate["method"] == selected["method"] and candidate["value"] == selected["value"])]
    else:
        selected = candidates[0]
    diagnostics.update({"context_id": target.context_id, "unit_id": target.unit_id,
                        "target_fact_candidates": [{"value": format_decimal(f.value), "context_id": f.context_id} for f in targets[:12]],
                        "method": selected.get("method"), "candidates": _json_safe(candidates)})
    return {"extracted_value": format_decimal(target.value), "calculated_value": format_decimal(selected["value"]),
            "status": "ok" if selected["method"] != "reported_fallback" else "fallback",
            "diagnostics": diagnostics}


def solve_record(record: dict[str, Any]) -> dict[str, Any]:
    query = record.get("query") or record.get("question") or record.get("input") or ""
    return solve(query)
