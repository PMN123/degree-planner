#!/usr/bin/env python3
"""Audit the local Purdue degree plan against encoded starter requirements."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - user environment guard
    yaml = None


ROOT = Path(__file__).resolve().parent


@dataclass
class CourseInstance:
    code: str
    title: str
    credits: float
    term: str
    source: str
    raw: dict[str, Any]

    @property
    def is_placeholder(self) -> bool:
        return self.code.startswith("PLACEHOLDER")

    @property
    def subject(self) -> str:
        return self.code.split()[0] if " " in self.code else self.code.split("-")[0]

    @property
    def number(self) -> int | None:
        match = re.search(r"\b([0-9]{3,5})\b", self.code)
        return int(match.group(1)) if match else None


def normalize_code(value: str) -> str:
    value = " ".join(str(value).strip().upper().replace("-", " ").split())
    match = re.match(r"^([A-Z]+)\s*([0-9][0-9A-Z]{2,5})$", value)
    if match:
        subject, number = match.groups()
        if number.isdigit() and len(number) < 5:
            number = number.zfill(5)
        return f"{subject} {number}"
    return value


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is required for audit_plan.py. Install with: pip install -r requirements.txt")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def fmt_credits(value: float) -> str:
    if abs(value - int(value)) < 0.001:
        return str(int(value))
    return f"{value:.1f}"


def load_completed(path: Path) -> list[CourseInstance]:
    data = load_json(path)
    courses = []
    for row in data.get("courses", []):
        courses.append(
            CourseInstance(
                code=normalize_code(row["code"]),
                title=row.get("title", ""),
                credits=float(row.get("credits", 0)),
                term=row.get("term", "Completed"),
                source=row.get("source", "Completed"),
                raw=row,
            )
        )
    return courses


def load_plan(path: Path) -> tuple[dict[str, Any], list[CourseInstance], list[str]]:
    plan = load_yaml(path)
    courses: list[CourseInstance] = []
    load_warnings: list[str] = []
    for semester in plan.get("semesters", []):
        term = semester["term"]
        expected = float(semester.get("credits", 0))
        actual = 0.0
        for row in semester.get("courses", []):
            credits = float(row.get("credits", 0))
            actual += credits
            courses.append(
                CourseInstance(
                    code=normalize_code(row["code"]),
                    title=row.get("title", ""),
                    credits=credits,
                    term=term,
                    source="Planned",
                    raw=row,
                )
            )
        if abs(actual - expected) > 0.001:
            load_warnings.append(f"{term}: declared {fmt_credits(expected)} credits but course entries sum to {fmt_credits(actual)}")
    return plan, courses, load_warnings


def course_credit_lookup(courses: list[CourseInstance], catalog: dict[str, Any]) -> dict[str, float]:
    lookup: dict[str, float] = {}
    for course in courses:
        lookup[course.code] = course.credits
    for code, record in catalog.get("courses", {}).items():
        credits = record.get("credits", {})
        if isinstance(credits, dict) and credits.get("max") is not None:
            lookup.setdefault(normalize_code(code), float(credits["max"]))
    return lookup


def available_codes(courses: list[CourseInstance], include_placeholders: bool = False) -> set[str]:
    return {course.code for course in courses if include_placeholders or not course.is_placeholder}


def select_one(codes: list[str], available: set[str]) -> tuple[bool, str | None, list[str]]:
    normalized = [normalize_code(code) for code in codes]
    for code in normalized:
        if code in available:
            return True, code, []
    return False, None, normalized


def evaluate_one_of_rules(rules: list[dict[str, Any]], available: set[str]) -> tuple[list[dict[str, Any]], set[str]]:
    results = []
    used: set[str] = set()
    for rule in rules:
        ok, selected, missing = select_one(rule["one_of"], available)
        if selected:
            used.add(selected)
        results.append(
            {
                "id": rule.get("id"),
                "label": rule.get("label", ", ".join(rule["one_of"])),
                "ok": ok,
                "selected": selected,
                "missing": missing,
            }
        )
    return results, used


def option_courses(option: dict[str, Any]) -> list[str]:
    codes = [normalize_code(code) for code in option.get("courses", [])]
    alternatives = [normalize_code(code) for code in option.get("alternatives", [])]
    if alternatives:
        # A compound alternative option is satisfied by its primary course list OR by one alternative.
        return codes + alternatives
    return codes


def option_satisfied(option: dict[str, Any], available: set[str]) -> tuple[bool, list[str]]:
    primary = [normalize_code(code) for code in option.get("courses", [])]
    alternatives = [normalize_code(code) for code in option.get("alternatives", [])]
    if primary and all(code in available for code in primary):
        return True, primary
    for alternative in alternatives:
        if alternative in available:
            return True, [alternative]
    return False, primary + alternatives


def choose_selectives(
    spec: dict[str, Any],
    available: set[str],
    used_required: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    used_required = used_required or set()
    chosen: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    category_counts: defaultdict[str, int] = defaultdict(int)
    max_by_category = spec.get("max_by_category", {})
    exclude_required = bool(spec.get("exclude_courses_used_by_required"))

    for option in spec.get("options", []):
        if len(chosen) >= int(spec.get("choose", 0)):
            break

        ok, selected_courses = option_satisfied(option, available)
        if not ok:
            skipped.append({"label": option.get("label"), "reason": "missing", "courses": selected_courses})
            continue

        if exclude_required and any(code in used_required for code in selected_courses):
            skipped.append({"label": option.get("label"), "reason": "already used as required in this track", "courses": selected_courses})
            continue

        category = option.get("category", "default")
        if category in max_by_category and category_counts[category] >= int(max_by_category[category]):
            skipped.append({"label": option.get("label"), "reason": f"category limit for {category}", "courses": selected_courses})
            continue

        category_counts[category] += 1
        chosen.append({"label": option.get("label"), "courses": selected_courses, "category": category})

    return chosen, skipped


def audit_cs(cs_req: dict[str, Any], all_courses: list[CourseInstance]) -> dict[str, Any]:
    available = available_codes(all_courses)
    math_results, math_used = evaluate_one_of_rules(cs_req.get("math_requirements", []), available)
    core = [normalize_code(code) for code in cs_req.get("core", [])]
    core_done = [code for code in core if code in available]
    core_missing = [code for code in core if code not in available]

    tracks = []
    usage: defaultdict[str, list[str]] = defaultdict(list)
    for code in math_used:
        usage[code].append("CS math")
    for code in core_done:
        usage[code].append("CS core")

    for track in cs_req.get("tracks", []):
        required_results, used_required = evaluate_one_of_rules(track.get("required", []), available)
        for code in used_required:
            usage[code].append(f"{track['label']} required")

        selected = []
        skipped = []
        if "selectives" in track:
            selected, skipped = choose_selectives(track["selectives"], available, used_required)
            for option in selected:
                for code in option["courses"]:
                    usage[code].append(f"{track['label']} selective")

        required_ok = all(item["ok"] for item in required_results)
        selected_ok = len(selected) >= int(track.get("selectives", {}).get("choose", 0))
        cs_course_count = None
        min_cs_ok = True
        if track.get("minimum_cs_courses"):
            counted_courses = set(used_required)
            for option in selected:
                counted_courses.update(option["courses"])
            cs_course_count = sum(1 for code in counted_courses if code.startswith("CS "))
            min_cs_ok = cs_course_count >= int(track["minimum_cs_courses"])

        tracks.append(
            {
                "id": track["id"],
                "label": track["label"],
                "ok": required_ok and selected_ok and min_cs_ok,
                "required": required_results,
                "selectives_chosen": selected,
                "selectives_needed": int(track.get("selectives", {}).get("choose", 0)),
                "selectives_skipped": skipped,
                "minimum_cs_courses": track.get("minimum_cs_courses"),
                "cs_course_count": cs_course_count,
                "notes": track.get("notes", []),
            }
        )

    return {
        "ok": all(item["ok"] for item in math_results) and not core_missing,
        "math": math_results,
        "core_done": core_done,
        "core_missing": core_missing,
        "tracks": tracks,
        "usage": dict(usage),
        "unmodeled_requirements": cs_req.get("unmodeled_requirements", []),
    }


def audit_math(math_req: dict[str, Any], all_courses: list[CourseInstance], credits: dict[str, float]) -> dict[str, Any]:
    available = available_codes(all_courses)
    required_results, used_required = evaluate_one_of_rules(math_req.get("required", []), available)

    preferred = [normalize_code(code) for code in math_req.get("selectives", {}).get("preferred", [])]
    required_selective_credits = float(math_req["selectives"]["credits_required"])
    group_rows = []
    for group in math_req.get("selectives", {}).get("groups", []):
        candidates = [normalize_code(code) for code in group["courses"] if normalize_code(code) in available]
        if not candidates:
            group_rows.append({"group": group["label"], "selected": None, "credits": 0, "available": [], "used": False})
            continue
        candidates.sort(key=lambda code: (0 if code in preferred else 1, preferred.index(code) if code in preferred else 99))
        chosen = candidates[0]
        group_rows.append({"group": group["label"], "selected": chosen, "credits": credits.get(chosen, 3), "available": candidates, "used": False})

    selected = []
    for row in group_rows:
        if row["selected"] in preferred and sum(float(item["credits"]) for item in selected) < required_selective_credits:
            row["used"] = True
            selected.append(row)
    for row in group_rows:
        if row["used"] or not row["selected"]:
            continue
        if sum(float(item["credits"]) for item in selected) >= required_selective_credits:
            break
        row["used"] = True
        selected.append(row)

    selective_credits = sum(float(row["credits"]) for row in selected)
    required_credits = sum(credits.get(code, 3) for code in used_required)

    usage: defaultdict[str, list[str]] = defaultdict(list)
    for code in used_required:
        usage[code].append("Math required")
    for row in group_rows:
        if row["selected"] and row["used"]:
            usage[row["selected"]].append("Math selective")

    return {
        "ok": all(item["ok"] for item in required_results) and selective_credits >= float(math_req["selectives"]["credits_required"]),
        "required": required_results,
        "selectives": group_rows,
        "selective_credits": selective_credits,
        "required_credits_estimate": required_credits,
        "usage": dict(usage),
    }


def audit_finance(fin_req: dict[str, Any], all_courses: list[CourseInstance]) -> dict[str, Any]:
    available = available_codes(all_courses)
    aliases = {normalize_code(k): normalize_code(v) for k, v in fin_req["electives"].get("catalog_alias_candidates", {}).items()}

    def resolve(code: str) -> str | None:
        normalized = normalize_code(code)
        if normalized in available:
            return normalized
        alias = aliases.get(normalized)
        if alias in available:
            return alias
        return None

    required = [normalize_code(code) for code in fin_req.get("required", [])]
    required_done = [resolved for code in required if (resolved := resolve(code))]
    required_missing = [code for code in required if not resolve(code)]

    preferred = [normalize_code(code) for code in fin_req.get("electives", {}).get("preferred", [])]
    options = [normalize_code(code) for code in fin_req.get("electives", {}).get("options", [])]
    available_options = [(code, resolve(code)) for code in options if resolve(code)]
    available_options.sort(key=lambda pair: (0 if pair[0] in preferred else 1, preferred.index(pair[0]) if pair[0] in preferred else 99))
    chosen = [resolved for _, resolved in available_options[: int(fin_req["electives"]["choose"])]]

    usage: defaultdict[str, list[str]] = defaultdict(list)
    for code in required_done:
        usage[code].append("Finance minor required")
    for code in chosen:
        usage[code].append("Finance minor elective")

    return {
        "ok": not required_missing and len(chosen) >= int(fin_req["electives"]["choose"]),
        "required_done": required_done,
        "required_missing": required_missing,
        "electives_chosen": chosen,
        "electives_missing_count": max(0, int(fin_req["electives"]["choose"]) - len(chosen)),
        "catalog_alias_candidates": aliases,
        "usage": dict(usage),
    }


def select_one_unused(codes: list[str], available: set[str], used: set[str]) -> tuple[bool, str | None, list[str]]:
    normalized = [normalize_code(code) for code in codes]
    for code in normalized:
        if code in available and code not in used:
            return True, code, []
    return False, None, normalized


def audit_statistics_math_emphasis(
    stats_req: dict[str, Any],
    all_courses: list[CourseInstance],
    credits: dict[str, float],
) -> dict[str, Any]:
    available = available_codes(all_courses)
    used_within_major: set[str] = set()
    usage: defaultdict[str, list[str]] = defaultdict(list)

    prereq_results, prereq_used = evaluate_one_of_rules(stats_req.get("pre_requisite_courses", []), available)
    for code in prereq_used:
        usage[code].append("Statistics prerequisite")

    required_results = []
    for rule in stats_req.get("required_major_courses", []):
        ok, selected, missing = select_one_unused(rule["one_of"], available, used_within_major)
        if selected:
            used_within_major.add(selected)
            usage[selected].append("Statistics required")
        required_results.append(
            {
                "id": rule.get("id"),
                "label": rule.get("label", ", ".join(rule["one_of"])),
                "ok": ok,
                "selected": selected,
                "missing": missing,
            }
        )

    advanced_selected = None
    advanced_spec = stats_req.get("advanced_math_selective", {})
    for code in [normalize_code(code) for code in advanced_spec.get("options", [])]:
        if code in available and code not in used_within_major and credits.get(code, 3) >= float(advanced_spec.get("minimum_credits", 0)):
            advanced_selected = code
            used_within_major.add(code)
            usage[code].append("Statistics advanced math selective")
            break

    stat_selected = None
    stat_selective_detail: dict[str, Any] = {"type": "course", "courses": []}
    stat_spec = stats_req.get("statistics_selective", {})
    for code in [normalize_code(code) for code in stat_spec.get("options", [])]:
        if code in available and code not in used_within_major:
            stat_selected = code
            used_within_major.add(code)
            usage[code].append("Statistics selective")
            stat_selective_detail = {"type": "course", "courses": [code]}
            break

    tdm_combo = stat_spec.get("tdm_combination", {})
    tdm_available = [
        normalize_code(code)
        for code in tdm_combo.get("courses", [])
        if normalize_code(code) in available and normalize_code(code) not in used_within_major
    ]
    tdm_credits = sum(credits.get(code, 1) for code in tdm_available)
    if not stat_selected and tdm_credits >= float(tdm_combo.get("credits_required", 0)):
        stat_selected = " + ".join(tdm_available)
        for code in tdm_available:
            used_within_major.add(code)
            usage[code].append("Statistics selective")
        stat_selective_detail = {"type": "tdm_combination", "courses": tdm_available, "credits": tdm_credits}

    selected_required = [row["selected"] for row in required_results if row.get("selected")]
    major_credits_estimate = sum(credits.get(code, 3) for code in selected_required)
    if advanced_selected:
        major_credits_estimate += credits.get(advanced_selected, 3)
    if stat_selective_detail["type"] == "course" and stat_selective_detail["courses"]:
        major_credits_estimate += credits.get(stat_selective_detail["courses"][0], 3)
    elif stat_selective_detail["type"] == "tdm_combination":
        major_credits_estimate += stat_selective_detail.get("credits", 0)

    missing_required = [row for row in required_results if not row["ok"]]
    return {
        "ok": bool(
            all(row["ok"] for row in prereq_results)
            and not missing_required
            and advanced_selected
            and stat_selected
        ),
        "program": stats_req.get("program"),
        "prerequisites": prereq_results,
        "required": required_results,
        "advanced_math_selective": {
            "selected": advanced_selected,
            "missing": advanced_selected is None,
            "options": [normalize_code(code) for code in advanced_spec.get("options", [])],
            "notes": advanced_spec.get("notes", []),
        },
        "statistics_selective": {
            "selected": stat_selected,
            "missing": stat_selected is None,
            "detail": stat_selective_detail,
            "options": [normalize_code(code) for code in stat_spec.get("options", [])],
            "tdm_available_credits": tdm_credits,
        },
        "major_credits_estimate": major_credits_estimate,
        "missing_required": missing_required,
        "usage": dict(usage),
        "unmodeled_requirements": stats_req.get("unmodeled_requirements", []),
    }


def catalog_status(catalog: dict[str, Any], planned: list[CourseInstance]) -> dict[str, Any]:
    records = catalog.get("courses", {})
    warnings = []
    aliases = []
    ok = []
    not_found = []

    for course in planned:
        if course.is_placeholder:
            continue
        record = records.get(course.code)
        if not record:
            warnings.append(f"{course.term}: {course.code} is not present in course_catalog.json")
            continue
        if record.get("status") == "ok":
            ok.append(course.code)
        else:
            not_found.append(course.code)
            warnings.append(f"{course.term}: {course.code} catalog status is {record.get('status')}")

    for code, record in records.items():
        if record.get("status") == "ok" and code != record.get("code"):
            aliases.append(f"{code} parsed as {record.get('code')}")

    return {"ok": sorted(set(ok)), "not_found": sorted(set(not_found)), "warnings": warnings, "aliases": aliases}


def starts_prereq_factor(token: dict[str, Any] | None) -> bool:
    return bool(token and token["type"] in {"COURSE", "ATTR", "LPAREN"})


def clean_prerequisite_text(text: str, current_code: str) -> str:
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = []
    index = 0
    while index < len(raw_lines):
        stripped = raw_lines[index]
        if stripped == "(" and index + 1 < len(raw_lines) and raw_lines[index + 1].startswith("Rule:"):
            index += 1
            continue
        rule_match = re.match(r"Rule:\s*(\d+):.*?for a total of\s+(\d+)\s+conditions?", stripped, flags=re.IGNORECASE)
        if rule_match:
            rule_id = rule_match.group(1)
            required_count = int(rule_match.group(2))
            rule_courses = []
            index += 1
            while index < len(raw_lines) and not raw_lines[index].startswith(f"End of rule {rule_id}"):
                if (
                    re.fullmatch(r"[A-Z]{2,5}", raw_lines[index])
                    and index + 1 < len(raw_lines)
                    and re.fullmatch(r"[A-Z]?[0-9]{3,5}", raw_lines[index + 1])
                ):
                    rule_courses.append(normalize_code(f"{raw_lines[index]} {raw_lines[index + 1]}"))
                    index += 2
                    continue
                index += 1
            if rule_courses:
                joiner = " or " if required_count == 1 else " and "
                lines.append(f"({joiner.join(rule_courses)})")
            if index < len(raw_lines) and raw_lines[index].startswith(f"End of rule {rule_id}"):
                index += 1
            continue
        if stripped.startswith("End of rule"):
            index += 1
            continue
        if stripped.endswith("Requisites"):
            index += 1
            continue
        if stripped == "General Requirements:":
            index += 1
            continue
        lines.append(stripped)
        index += 1

    cleaned = " ".join(lines)
    cleaned = re.sub(r"\bCourse or Test:\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bStudent Attribute:\s*GR\b", " ATTR_GR ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bMinimum Grade of\s+[A-Z0-9+\-]+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bUndergraduate level\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bGraduate level\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bProfessional level\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bMay not be taken concurrently\.?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(re.escape(current_code), " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def tokenize_prerequisite_text(text: str, current_code: str) -> tuple[list[dict[str, Any]], list[str]]:
    cleaned = clean_prerequisite_text(text, current_code)
    if not cleaned:
        return [], []

    token_re = re.compile(
        r"ATTR_GR|[()]|\bAND\b|\bOR\b|\b[A-Z]{2,5}\s+[A-Z]?[0-9]{3,5}\b",
        flags=re.IGNORECASE,
    )
    matches = list(token_re.finditer(cleaned))
    tokens: list[dict[str, Any]] = []
    warnings: list[str] = []

    for idx, match in enumerate(matches):
        raw = match.group(0)
        upper = raw.upper()
        if upper == "(":
            tokens.append({"type": "LPAREN"})
        elif upper == ")":
            tokens.append({"type": "RPAREN"})
        elif upper == "AND":
            tokens.append({"type": "AND"})
        elif upper == "OR":
            tokens.append({"type": "OR"})
        elif upper == "ATTR_GR":
            tokens.append({"type": "ATTR", "value": "GR"})
        else:
            code = normalize_code(raw)
            if code == current_code:
                continue
            next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(cleaned)
            following = cleaned[match.end() : next_start].lower()
            concurrent_allowed = "may be taken concurrently" in following and "may not be taken concurrently" not in following
            tokens.append({"type": "COURSE", "value": code, "concurrent_allowed": concurrent_allowed})

    leftover = token_re.sub(" ", cleaned)
    leftover = re.sub(r"\bmay be taken concurrently\b", " ", leftover, flags=re.IGNORECASE)
    leftover = re.sub(r"\[[^\]]+\]", " ", leftover)
    ignored = [part for part in re.split(r"\s+", leftover.strip()) if part and part not in {"-", ":"}]
    if ignored:
        warnings.append(f"ignored non-structural prerequisite text: {' '.join(ignored[:12])}")
    return tokens, warnings


def parse_prerequisite_tokens(tokens: list[dict[str, Any]], and_precedence: bool = False) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    index = 0

    def peek() -> dict[str, Any] | None:
        return tokens[index] if index < len(tokens) else None

    def consume(expected: str | None = None) -> dict[str, Any] | None:
        nonlocal index
        token = peek()
        if token is None:
            return None
        if expected and token["type"] != expected:
            return None
        index += 1
        return token

    def parse_factor() -> dict[str, Any] | None:
        token = peek()
        if token is None:
            return None
        if token["type"] == "LPAREN":
            consume("LPAREN")
            nested = parse_expression()
            if peek() and peek()["type"] == "RPAREN":
                consume("RPAREN")
            else:
                warnings.append("missing closing parenthesis in prerequisite expression")
            return nested
        if token["type"] == "COURSE":
            consume()
            return {
                "type": "COURSE",
                "code": token["value"],
                "concurrent_allowed": bool(token.get("concurrent_allowed")),
            }
        if token["type"] == "ATTR":
            consume()
            return {"type": "ATTR", "attribute": token["value"]}
        warnings.append(f"unexpected token {token['type']} in prerequisite expression")
        consume()
        return None

    def parse_or_high() -> dict[str, Any] | None:
        left = parse_factor()
        while True:
            token = peek()
            if not token or token["type"] != "OR":
                break
            consume("OR")
            right = parse_factor()
            if right is not None:
                left = {"type": "OR", "children": [child for child in [left, right] if child is not None]}
        return left

    def parse_and_low() -> dict[str, Any] | None:
        left = parse_or_high()
        while True:
            token = peek()
            if token and token["type"] == "AND":
                consume("AND")
                right = parse_or_high()
                if right is not None:
                    left = {"type": "AND", "children": [child for child in [left, right] if child is not None]}
                continue
            if starts_prereq_factor(token):
                right = parse_or_high()
                if right is not None:
                    left = {"type": "AND", "children": [child for child in [left, right] if child is not None]}
                continue
            break
        return left

    def parse_and_high() -> dict[str, Any] | None:
        left = parse_factor()
        while True:
            token = peek()
            if token and token["type"] == "AND":
                consume("AND")
                right = parse_factor()
                if right is not None:
                    left = {"type": "AND", "children": [child for child in [left, right] if child is not None]}
                continue
            if starts_prereq_factor(token):
                right = parse_factor()
                if right is not None:
                    left = {"type": "AND", "children": [child for child in [left, right] if child is not None]}
                continue
            break
        return left

    def parse_or_low() -> dict[str, Any] | None:
        left = parse_and_high()
        while True:
            token = peek()
            if not token or token["type"] != "OR":
                break
            consume("OR")
            right = parse_and_high()
            if right is not None:
                left = {"type": "OR", "children": [child for child in [left, right] if child is not None]}
        return left

    def parse_expression() -> dict[str, Any] | None:
        return parse_or_low() if and_precedence else parse_and_low()

    expr = parse_expression()
    while index < len(tokens):
        token = consume()
        if token and token["type"] != "RPAREN":
            warnings.append(f"unconsumed token {token['type']} in prerequisite expression")
    return expr, warnings


def atom_label(atom: dict[str, Any]) -> str:
    if atom["type"] == "COURSE":
        suffix = " (concurrent allowed)" if atom.get("concurrent_allowed") else ""
        return f"{atom['code']}{suffix}"
    if atom["type"] == "ATTR":
        return f"student attribute {atom['attribute']}"
    return str(atom)


def prerequisite_expression_to_string(expr: dict[str, Any] | None) -> str:
    if expr is None:
        return ""
    if expr["type"] in {"COURSE", "ATTR"}:
        return atom_label(expr)
    op = f" {expr['type']} "
    parts = []
    for child in expr.get("children", []):
        rendered = prerequisite_expression_to_string(child)
        if child.get("type") in {"AND", "OR"} and child.get("type") != expr["type"]:
            rendered = f"({rendered})"
        parts.append(rendered)
    return op.join(part for part in parts if part)


def prerequisite_alternatives(expr: dict[str, Any] | None, limit: int = 500) -> list[list[dict[str, Any]]]:
    if expr is None:
        return []
    if expr["type"] in {"COURSE", "ATTR"}:
        return [[expr]]
    if expr["type"] == "OR":
        alternatives: list[list[dict[str, Any]]] = []
        for child in expr.get("children", []):
            alternatives.extend(prerequisite_alternatives(child, limit=limit))
            if len(alternatives) > limit:
                return alternatives[:limit]
        return alternatives
    if expr["type"] == "AND":
        alternatives = [[]]
        for child in expr.get("children", []):
            child_alternatives = prerequisite_alternatives(child, limit=limit)
            if not child_alternatives:
                continue
            combined = []
            for base in alternatives:
                for child_alt in child_alternatives:
                    merged: list[dict[str, Any]] = []
                    seen = set()
                    for atom in base + child_alt:
                        key = (atom.get("type"), atom.get("code"), atom.get("attribute"), atom.get("concurrent_allowed"))
                        if key not in seen:
                            seen.add(key)
                            merged.append(atom)
                    combined.append(merged)
                    if len(combined) > limit:
                        break
                if len(combined) > limit:
                    break
            alternatives = combined[:limit]
        return alternatives
    return []


def prerequisite_atom_satisfied(
    atom: dict[str, Any],
    completed_before_term: set[str],
    same_term_courses: set[str],
    student_attributes: set[str],
) -> bool:
    if atom["type"] == "ATTR":
        return atom["attribute"] in student_attributes
    if atom["type"] == "COURSE":
        code = atom["code"]
        return code in completed_before_term or (atom.get("concurrent_allowed") and code in same_term_courses)
    return False


def evaluate_prerequisite_expression(
    expr: dict[str, Any] | None,
    completed_before_term: set[str],
    same_term_courses: set[str],
    student_attributes: set[str],
) -> dict[str, Any]:
    alternatives = prerequisite_alternatives(expr)
    if not alternatives:
        return {
            "satisfied": True,
            "satisfied_by": [],
            "missing_best_alternative": [],
            "alternatives_checked": 0,
        }

    best_missing: list[dict[str, Any]] | None = None
    best_satisfied: list[dict[str, Any]] = []
    best_score: tuple[int, int, int] | None = None
    for alternative in alternatives:
        missing = [
            atom
            for atom in alternative
            if not prerequisite_atom_satisfied(atom, completed_before_term, same_term_courses, student_attributes)
        ]
        if not missing:
            return {
                "satisfied": True,
                "satisfied_by": [atom_label(atom) for atom in alternative],
                "missing_best_alternative": [],
                "alternatives_checked": len(alternatives),
            }
        attr_missing_count = sum(1 for atom in missing if atom["type"] == "ATTR")
        satisfied_count = len(alternative) - len(missing)
        score = (len(missing), attr_missing_count, -satisfied_count)
        if best_score is None or score < best_score:
            best_score = score
            best_missing = missing
            best_satisfied = [
                atom
                for atom in alternative
                if prerequisite_atom_satisfied(atom, completed_before_term, same_term_courses, student_attributes)
            ]

    return {
        "satisfied": False,
        "satisfied_by": [atom_label(atom) for atom in best_satisfied],
        "missing_best_alternative": [atom_label(atom) for atom in (best_missing or [])],
        "alternatives_checked": len(alternatives),
    }


def audit_prerequisites(
    catalog: dict[str, Any],
    completed: list[CourseInstance],
    planned: list[CourseInstance],
    student_attributes: set[str] | None = None,
) -> dict[str, Any]:
    student_attributes = student_attributes or set()
    records = catalog.get("courses", {})
    completed_before_term = {course.code for course in completed if not course.is_placeholder}
    checks: list[dict[str, Any]] = []

    planned_by_term: dict[str, list[CourseInstance]] = defaultdict(list)
    for course in planned:
        planned_by_term[course.term].append(course)

    for term, term_courses in planned_by_term.items():
        same_term_codes = {course.code for course in term_courses if not course.is_placeholder}
        for course in term_courses:
            if course.is_placeholder:
                continue
            record = records.get(course.code)
            if not record:
                checks.append(
                    {
                        "term": term,
                        "code": course.code,
                        "title": course.title,
                        "status": "missing_catalog",
                        "ok": False,
                        "warnings": ["course is not present in course_catalog.json"],
                    }
                )
                continue
            if record.get("status") != "ok":
                checks.append(
                    {
                        "term": term,
                        "code": course.code,
                        "title": course.title,
                        "status": "catalog_not_ok",
                        "ok": False,
                        "warnings": [f"catalog status is {record.get('status')}"],
                    }
                )
                continue

            prereq_text = record.get("prerequisites_text") or ""
            if not prereq_text.strip():
                checks.append(
                    {
                        "term": term,
                        "code": course.code,
                        "title": record.get("title") or course.title,
                        "status": "no_prerequisites",
                        "ok": True,
                        "satisfied": True,
                        "warnings": [],
                    }
                )
                continue

            tokens, tokenize_warnings = tokenize_prerequisite_text(prereq_text, course.code)
            expr, parse_warnings = parse_prerequisite_tokens(tokens, and_precedence="Rule:" in prereq_text)
            evaluation = evaluate_prerequisite_expression(expr, completed_before_term, same_term_codes, student_attributes)
            checks.append(
                {
                    "term": term,
                    "code": course.code,
                    "title": record.get("title") or course.title,
                    "status": "ok" if evaluation["satisfied"] else "unsatisfied",
                    "ok": bool(evaluation["satisfied"]),
                    "satisfied": bool(evaluation["satisfied"]),
                    "expression": prerequisite_expression_to_string(expr),
                    "satisfied_by": evaluation["satisfied_by"],
                    "missing_best_alternative": evaluation["missing_best_alternative"],
                    "alternatives_checked": evaluation["alternatives_checked"],
                    "warnings": tokenize_warnings + parse_warnings,
                    "raw_prerequisites_text": prereq_text,
                }
            )
        completed_before_term.update(same_term_codes)

    failed = [check for check in checks if not check.get("ok")]
    warnings = [
        f"{check['term']}: {check['code']} {check['status']}: {', '.join(check.get('missing_best_alternative') or check.get('warnings') or [])}"
        for check in failed
    ]
    return {"ok": not failed, "checks": checks, "failed": failed, "warnings": warnings}


def normalize_restriction_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def restriction_profile(plan: dict[str, Any]) -> dict[str, set[str]]:
    profile = plan.get("student_profile", {})
    programs = {normalize_restriction_value(value) for value in profile.get("programs", [])}
    majors = {normalize_restriction_value(value) for value in profile.get("majors", [])}
    minors = {normalize_restriction_value(value) for value in profile.get("minors", [])}
    fields = {normalize_restriction_value(value) for value in profile.get("fields_of_study", [])}
    fields.update(majors)
    fields.update(minors)
    return {
        "program": programs,
        "major": majors,
        "field": fields,
        "minor": minors,
        "planned_overrides": {normalize_code(value) for value in profile.get("planned_restriction_overrides", [])},
    }


def parse_restriction_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = None
        if line.startswith("Must be enrolled in one of the following"):
            header = ("must", line)
        elif line.startswith("May not be enrolled in one of the following"):
            header = ("may_not", line)
        elif line.startswith("May not be enrolled as the following"):
            header = ("may_not", line)

        if header:
            if current:
                blocks.append(current)
            mode, header_text = header
            lowered = header_text.lower()
            if "program" in lowered:
                category = "program"
            elif "field" in lowered:
                category = "field"
            elif "major" in lowered:
                category = "major"
            elif "classification" in lowered:
                category = "classification"
            else:
                category = "unknown"
            current = {"mode": mode, "category": category, "header": header_text, "values": []}
            continue

        if current:
            current["values"].append(line)

    if current:
        blocks.append(current)
    return blocks


def classification_from_credits(credits: float) -> str:
    if credits < 30:
        return "Freshman"
    if credits < 60:
        return "Sophomore"
    if credits < 90:
        return "Junior"
    return "Senior"


def classification_value_matches(value: str, credits: float) -> bool:
    range_match = re.search(r"(\d+)\s*-\s*(\d+)\s*hours", value)
    if range_match:
        low = int(range_match.group(1))
        high = int(range_match.group(2))
        return low <= credits <= high
    classification = classification_from_credits(credits).lower()
    return classification in value.lower()


def evaluate_restriction_block(block: dict[str, Any], profile: dict[str, set[str]], credits_before_term: float) -> dict[str, Any]:
    values = block.get("values", [])
    normalized_values = {normalize_restriction_value(value) for value in values}
    mode = block.get("mode")
    category = block.get("category")

    if category == "classification":
        matched = [value for value in values if classification_value_matches(value, credits_before_term)]
        ok = not matched if mode == "may_not" else bool(matched)
        return {
            "ok": ok,
            "mode": mode,
            "category": category,
            "values": values,
            "matched": matched,
            "student_values": [f"{classification_from_credits(credits_before_term)} ({fmt_credits(credits_before_term)} credits before term)"],
            "reason": None if ok else f"classification/credit standing matches restricted value: {', '.join(matched)}",
        }

    student_values = profile.get(category, set())
    matched = sorted(student_values.intersection(normalized_values))
    if mode == "must":
        ok = bool(matched)
        reason = None if ok else f"requires one of: {', '.join(values)}"
    elif mode == "may_not":
        ok = not matched
        reason = None if ok else f"restricted for: {', '.join(matched)}"
    else:
        ok = True
        reason = "unrecognized restriction mode; not enforced"

    return {
        "ok": ok,
        "mode": mode,
        "category": category,
        "values": values,
        "matched": matched,
        "student_values": sorted(student_values),
        "reason": reason,
    }


def audit_restrictions(catalog: dict[str, Any], completed: list[CourseInstance], planned: list[CourseInstance], plan: dict[str, Any]) -> dict[str, Any]:
    records = catalog.get("courses", {})
    profile = restriction_profile(plan)
    completed_credits_before_term = sum(course.credits for course in completed)
    checks: list[dict[str, Any]] = []

    planned_by_term: dict[str, list[CourseInstance]] = defaultdict(list)
    for course in planned:
        planned_by_term[course.term].append(course)

    for term, term_courses in planned_by_term.items():
        for course in term_courses:
            if course.is_placeholder:
                continue
            record = records.get(course.code)
            if not record:
                checks.append(
                    {
                        "term": term,
                        "code": course.code,
                        "title": course.title,
                        "status": "missing_catalog",
                        "ok": False,
                        "blocks": [],
                        "warnings": ["course is not present in course_catalog.json"],
                    }
                )
                continue
            if record.get("status") != "ok":
                checks.append(
                    {
                        "term": term,
                        "code": course.code,
                        "title": course.title,
                        "status": "catalog_not_ok",
                        "ok": False,
                        "blocks": [],
                        "warnings": [f"catalog status is {record.get('status')}"],
                    }
                )
                continue
            restrictions_text = record.get("restrictions_text") or ""
            if not restrictions_text.strip():
                checks.append(
                    {
                        "term": term,
                        "code": course.code,
                        "title": record.get("title") or course.title,
                        "status": "no_restrictions",
                        "ok": True,
                        "blocks": [],
                        "warnings": [],
                    }
                )
                continue

            blocks = parse_restriction_blocks(restrictions_text)
            block_results = [evaluate_restriction_block(block, profile, completed_credits_before_term) for block in blocks]
            failed_blocks = [block for block in block_results if not block.get("ok")]
            override_planned = course.code in profile.get("planned_overrides", set())
            effective_failed_blocks = [] if override_planned else failed_blocks
            warnings = []
            if override_planned and failed_blocks:
                warnings.append("Restriction override/permit required and marked as planned.")
            checks.append(
                {
                    "term": term,
                    "code": course.code,
                    "title": record.get("title") or course.title,
                    "status": "override_planned" if override_planned and failed_blocks else ("ok" if not failed_blocks else "restricted"),
                    "ok": not effective_failed_blocks,
                    "override_planned": override_planned,
                    "override_required": bool(failed_blocks),
                    "blocks": block_results,
                    "warnings": warnings if warnings else ([] if blocks else ["restriction text was present but no supported restriction blocks were parsed"]),
                    "raw_restrictions_text": restrictions_text,
                    "credits_before_term": completed_credits_before_term,
                }
            )

        completed_credits_before_term += sum(course.credits for course in term_courses)

    failed = [check for check in checks if not check.get("ok")]
    warnings = []
    for check in failed:
        reasons = []
        for block in check.get("blocks", []):
            if not block.get("ok") and block.get("reason"):
                reasons.append(block["reason"])
        warnings.append(f"{check['term']}: {check['code']} restricted: {'; '.join(reasons or check.get('warnings', []))}")
    return {"ok": not failed, "checks": checks, "failed": failed, "warnings": warnings}


def semester_loads(planned: list[CourseInstance]) -> list[dict[str, Any]]:
    loads: defaultdict[str, float] = defaultdict(float)
    for course in planned:
        loads[course.term] += course.credits
    return [{"term": term, "credits": credits, "warning": credits > 18 or credits >= 18} for term, credits in loads.items()]


def upper_level_resident_estimate(completed: list[CourseInstance], planned: list[CourseInstance]) -> float:
    total = 0.0
    for course in completed + planned:
        if course.source not in {"Purdue", "Planned"}:
            continue
        number = course.number
        if number is not None and number >= 30000 and not course.is_placeholder:
            total += course.credits
    return total


def merge_usage(*usage_maps: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: defaultdict[str, list[str]] = defaultdict(list)
    for usage in usage_maps:
        for code, labels in usage.items():
            merged[code].extend(labels)
    return dict(sorted(merged.items()))


def build_audit(args: argparse.Namespace) -> dict[str, Any]:
    completed = load_completed(ROOT / args.completed)
    plan, planned, plan_warnings = load_plan(ROOT / args.plan)
    catalog_path = ROOT / args.catalog
    catalog = load_json(catalog_path) if catalog_path.exists() else {"courses": {}}
    all_courses = completed + planned
    credit_lookup = course_credit_lookup(all_courses, catalog)

    cs = audit_cs(load_json(ROOT / "requirements" / "cs.json"), all_courses)
    math = audit_math(load_json(ROOT / "requirements" / "math.json"), all_courses, credit_lookup)
    finance = audit_finance(load_json(ROOT / "requirements" / "finance_minor.json"), all_courses)
    statistics = audit_statistics_math_emphasis(load_json(ROOT / "requirements" / "statistics_math_emphasis.json"), all_courses, credit_lookup)
    catalog_check = catalog_status(catalog, planned)
    prereq = audit_prerequisites(catalog, completed, planned)
    restrictions = audit_restrictions(catalog, completed, planned, plan)

    total_completed = sum(course.credits for course in completed)
    total_planned = sum(course.credits for course in planned)
    placeholders = [course for course in planned if course.is_placeholder]
    target = float(load_json(ROOT / "requirements" / "cs.json")["degree_total_credits"])

    usage = merge_usage(cs["usage"], math["usage"], finance["usage"], statistics["usage"])
    overlaps = {code: labels for code, labels in usage.items() if len(labels) > 1}
    base_feasible = bool(
        cs["ok"]
        and math["ok"]
        and finance["ok"]
        and prereq["ok"]
        and restrictions["ok"]
        and (total_completed + total_planned >= target)
    )

    return {
        "target_graduation": plan.get("target_graduation"),
        "plan_warnings": plan_warnings,
        "credits": {
            "completed_listed": total_completed,
            "planned": total_planned,
            "total_if_all_count": total_completed + total_planned,
            "degree_target": target,
            "credit_target_met": total_completed + total_planned >= target,
            "placeholder_credits": sum(course.credits for course in placeholders),
        },
        "loads": semester_loads(planned),
        "upper_level_resident_estimate": upper_level_resident_estimate(completed, planned),
        "cs": cs,
        "math": math,
        "finance": finance,
        "statistics_math_emphasis": statistics,
        "catalog": catalog_check,
        "prerequisite_checks": prereq["checks"],
        "prerequisite_warnings": prereq["warnings"],
        "prerequisites_ok": prereq["ok"],
        "restriction_checks": restrictions["checks"],
        "restriction_warnings": restrictions["warnings"],
        "restrictions_ok": restrictions["ok"],
        "overlaps": overlaps,
        "base_feasible_by_fall_2028": base_feasible,
        "feasible_by_fall_2028": bool(base_feasible and statistics["ok"]),
    }


def print_rule_results(label: str, rows: list[dict[str, Any]]) -> None:
    print(label)
    for row in rows:
        status = "OK" if row["ok"] else "MISSING"
        selected = f" -> {row['selected']}" if row.get("selected") else ""
        missing = f" (needs one of: {', '.join(row['missing'])})" if row.get("missing") and not row["ok"] else ""
        print(f"  [{status}] {row['label']}{selected}{missing}")


def print_audit(audit: dict[str, Any]) -> None:
    print("Degree plan audit")
    print(f"Target graduation: {audit['target_graduation']}")
    print()

    credits = audit["credits"]
    print("Credits")
    print(f"  Completed listed: {fmt_credits(credits['completed_listed'])}")
    print(f"  Planned: {fmt_credits(credits['planned'])}")
    print(f"  Total if all listed credits count: {fmt_credits(credits['total_if_all_count'])} / {fmt_credits(credits['degree_target'])}")
    print(f"  Placeholder credits still requiring exact requirement mapping: {fmt_credits(credits['placeholder_credits'])}")
    print(f"  Estimated Purdue upper-level resident credits from encoded data: {fmt_credits(audit['upper_level_resident_estimate'])}")
    print()

    print("Semester loads")
    for row in audit["loads"]:
        marker = "CHECK" if row["warning"] else "OK"
        print(f"  [{marker}] {row['term']}: {fmt_credits(row['credits'])} credits")
    print()

    print("CS major")
    print_rule_results("  Math requirements", audit["cs"]["math"])
    print(f"  Core done: {', '.join(audit['cs']['core_done']) if audit['cs']['core_done'] else 'none'}")
    print(f"  Core missing: {', '.join(audit['cs']['core_missing']) if audit['cs']['core_missing'] else 'none'}")
    for track in audit["cs"]["tracks"]:
        status = "OK" if track["ok"] else "MISSING"
        print(f"  [{status}] {track['label']}")
        for row in track["required"]:
            req_status = "OK" if row["ok"] else "MISSING"
            selected = f" -> {row['selected']}" if row.get("selected") else ""
            print(f"    [{req_status}] {row['label']}{selected}")
        chosen = ", ".join(item["label"] for item in track["selectives_chosen"]) or "none"
        print(f"    Selectives: {len(track['selectives_chosen'])}/{track['selectives_needed']} -> {chosen}")
        if track["minimum_cs_courses"]:
            print(f"    CS-course count: {track['cs_course_count']}/{track['minimum_cs_courses']}")
        for note in track.get("notes", []):
            print(f"    Note: {note}")
    print()

    print("Math major")
    print_rule_results("  Required", audit["math"]["required"])
    print(f"  Selective credits: {fmt_credits(audit['math']['selective_credits'])}/9")
    for row in audit["math"]["selectives"]:
        if row["selected"] and row.get("used"):
            selected = row["selected"]
        elif row["selected"]:
            selected = f"not used (available: {', '.join(row['available'])})"
        else:
            selected = "missing"
        print(f"    {row['group']}: {selected}")
    print()

    print("Finance minor")
    print(f"  Required done: {', '.join(audit['finance']['required_done']) if audit['finance']['required_done'] else 'none'}")
    print(f"  Required missing: {', '.join(audit['finance']['required_missing']) if audit['finance']['required_missing'] else 'none'}")
    print(f"  Electives chosen: {', '.join(audit['finance']['electives_chosen']) if audit['finance']['electives_chosen'] else 'none'}")
    if audit["finance"]["catalog_alias_candidates"]:
        for src, dst in audit["finance"]["catalog_alias_candidates"].items():
            print(f"  Catalog alias candidate: {src} -> {dst}")
    print()

    stats = audit["statistics_math_emphasis"]
    print("Statistics: Math Emphasis BS")
    print_rule_results("  Required prerequisite courses", stats["prerequisites"])
    print_rule_results("  Required major courses", stats["required"])
    advanced = stats["advanced_math_selective"]["selected"] or "missing"
    stat_sel = stats["statistics_selective"]["selected"] or "missing"
    print(f"  Advanced math selective: {advanced}")
    print(f"  Statistics selective: {stat_sel}")
    if stats["statistics_selective"]["missing"]:
        print(f"  TDM selective progress: {fmt_credits(stats['statistics_selective']['tdm_available_credits'])}/3 credits available")
    print(f"  Major credits identified from encoded courses: {fmt_credits(stats['major_credits_estimate'])}")
    print()

    print("Catalog checks")
    print(f"  Planned catalog hits: {len(audit['catalog']['ok'])}")
    if audit["catalog"]["not_found"]:
        print(f"  Not found among planned courses: {', '.join(audit['catalog']['not_found'])}")
    for warning in audit["catalog"]["warnings"]:
        print(f"  Warning: {warning}")
    for alias in audit["catalog"]["aliases"]:
        print(f"  Parsed alias: {alias}")
    print()

    print("Prerequisite checks")
    for check in audit["prerequisite_checks"]:
        marker = "OK" if check.get("ok") else "BLOCKED"
        if check["status"] == "no_prerequisites":
            detail = "no catalog prerequisites"
        elif check.get("ok"):
            satisfied_by = ", ".join(check.get("satisfied_by") or [])
            detail = f"satisfied by {satisfied_by}" if satisfied_by else "satisfied"
        else:
            missing = ", ".join(check.get("missing_best_alternative") or check.get("warnings") or [])
            detail = f"missing {missing}" if missing else check["status"]
        print(f"  [{marker}] {check['term']} {check['code']}: {detail}")
        for warning in check.get("warnings", []):
            print(f"    Parser note: {warning}")
    print()

    print("Restriction checks")
    for check in audit["restriction_checks"]:
        marker = "OK" if check.get("ok") else "BLOCKED"
        if check["status"] == "no_restrictions":
            detail = "no catalog restrictions"
        elif check.get("override_planned") and check.get("override_required"):
            reasons = [block.get("reason") for block in check.get("blocks", []) if not block.get("ok") and block.get("reason")]
            detail = f"override/permit required; treated as planned ({'; '.join(reasons)})"
        elif check.get("ok"):
            matched_parts = []
            for block in check.get("blocks", []):
                if block.get("mode") == "must":
                    matched = ", ".join(block.get("matched") or [])
                    matched_parts.append(f"{block.get('category')} match: {matched}")
                elif block.get("mode") == "may_not":
                    matched_parts.append(f"{block.get('category')} exclusion clear")
            detail = "; ".join(matched_parts) if matched_parts else "restrictions satisfied"
        else:
            reasons = [block.get("reason") for block in check.get("blocks", []) if not block.get("ok") and block.get("reason")]
            detail = "; ".join(reasons or check.get("warnings") or [check["status"]])
        print(f"  [{marker}] {check['term']} {check['code']}: {detail}")
        for warning in check.get("warnings", []):
            print(f"    Parser note: {warning}")
    print()

    print("Overlap report")
    if audit["overlaps"]:
        for code, labels in audit["overlaps"].items():
            print(f"  {code}: {', '.join(labels)}")
    else:
        print("  No overlapping requirement uses selected.")
    print()

    print("Unmodeled / advisor verification needed")
    for item in audit["cs"]["unmodeled_requirements"]:
        print(f"  - {item}")
    for item in audit["statistics_math_emphasis"]["unmodeled_requirements"]:
        print(f"  - Statistics Math Emphasis: {item}")
    if audit["plan_warnings"]:
        for warning in audit["plan_warnings"]:
            print(f"  - Plan warning: {warning}")
    print()

    base_status = "YES" if audit["base_feasible_by_fall_2028"] else "NOT YET"
    status = "YES" if audit["feasible_by_fall_2028"] else "NOT YET"
    print(f"Fall 2028 feasibility for original CS/Math/Finance plan: {base_status}")
    print(f"Fall 2028 feasibility with Statistics Math Emphasis added: {status}")
    if audit["feasible_by_fall_2028"]:
        print("Smallest summer-course plan from encoded rules: none required, assuming placeholders map to remaining core requirements and catalog/enrollment issues are resolved.")
    else:
        print("Smallest summer-course plan from encoded rules: inspect missing items above; not automatically generated until hard blockers are resolved.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completed", default="completed_courses.json")
    parser.add_argument("--plan", default="plan.yaml")
    parser.add_argument("--catalog", default="course_catalog.json")
    parser.add_argument("--json-output", default=None, help="Optional path to write machine-readable audit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        audit = build_audit(args)
    except Exception as exc:  # pragma: no cover - top-level CLI guard
        print(f"audit failed: {exc}", file=sys.stderr)
        return 1
    print_audit(audit)
    if args.json_output:
        output_path = Path(args.json_output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
