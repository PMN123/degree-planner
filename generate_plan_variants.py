#!/usr/bin/env python3
"""Generate fast graduation plan variants from the local encoded requirements.

The generated plans intentionally use the same local catalog/audit assumptions as
audit_plan.py. They do not prove future section availability.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from audit_plan import (
    CourseInstance,
    audit_cs,
    audit_finance,
    audit_math,
    audit_prerequisites,
    audit_restrictions,
    audit_statistics_math_emphasis,
    course_credit_lookup,
    load_completed,
    load_json,
    normalize_code,
)


ROOT = Path(__file__).resolve().parent
TERMS = ["Fall 2026", "Spring 2027", "Fall 2027", "Spring 2028", "Fall 2028", "Spring 2029"]
PREFERRED_CAP = 20
OVERLOAD_CAP = 21
DEGREE_TARGET_CREDITS = 120


TRACKS: dict[str, dict[str, Any]] = {
    "algorithms": {
        "label": "Algorithmic Foundations",
        "audit_id": "algorithmic_foundations",
        "courses": ["CS 38100", "CS 35400", "CS 37300", "CS 31400", "CS 44800"],
        "notes": ["MA 35301 is already completed and is used as the third Algorithms selective."],
    },
    "machine_intelligence": {
        "label": "Machine Intelligence",
        "audit_id": "machine_intelligence",
        "courses": ["CS 37300", "CS 38100", "CS 47100", "MA 41600", "CS 31400", "CS 44800"],
    },
    "cse": {
        "label": "Computational Science and Engineering",
        "audit_id": "computational_science_and_engineering",
        "courses": ["CS 31400", "CS 38100", "MA 36600", "CS 37300", "CS 35400", "MA 34100", "IE 33500"],
        "notes": ["Uses IE 33500 instead of a 500-level CS selective to avoid extra graduate-course friction."],
    },
}

EXTRAS: dict[str, dict[str, Any]] = {
    "math": {
        "label": "Mathematics BS",
        "courses": ["MA 36600", "MA 42500", "MA 34100", "MA 45300"],
        "notes": ["MA 26500 and MA 35301 are already completed; CS 24000, CS 31400, and CS 38100 cover the encoded Math selective credits."],
    },
    "statistics": {
        "label": "Statistics BS, Math Emphasis",
        "courses": ["MA 41600", "STAT 41700", "MA 43200", "STAT 51200", "MA 34100", "MA 36600"],
        "notes": ["STAT 35000, MA 26500, MA 35301, and MA 26100 are already completed; CS 37300 covers the encoded Statistics selective."],
    },
    "finance": {
        "label": "Finance minor",
        "courses": ["FIN 30400", "FIN 41100", "FIN 41300", "FIN 41150", "FIN 41650"],
        "notes": ["Upper-level FIN courses are marked as planned overrides/permits, matching the existing audit model."],
    },
}

CS_CORE_REMAINING = ["CS 18200", "CS 24000", "CS 25000", "CS 25100", "CS 25200"]
FINANCE_OVERRIDES = ["FIN 41100", "FIN 41300", "FIN 41150", "FIN 41650"]

# Simplified prerequisite model for the chosen courses. These are the alternatives
# selected by the generated plans, not a replacement for the full audit parser.
PREREQS: dict[str, list[str]] = {
    "CS 18200": ["CS 18000", "MA 16500"],
    "CS 24000": ["CS 18000"],
    "CS 25000": ["CS 18200", "CS 24000"],
    "CS 25100": ["CS 18200", "CS 24000"],
    "CS 25200": ["CS 25000", "CS 25100"],
    "CS 31400": ["CS 18000", "MA 26500"],
    "CS 35400": ["CS 25100", "CS 25200"],
    "CS 37300": ["CS 18200", "CS 25100", "STAT 35000"],
    "CS 38100": ["CS 25100", "MA 26100"],
    "CS 44800": ["CS 25100"],
    "CS 47100": ["CS 25100"],
    "IE 33500": ["MA 26500"],
    "MA 34100": ["MA 26100"],
    "MA 36600": ["MA 26500"],
    "MA 41600": ["MA 26100"],
    "MA 42500": ["MA 26500"],
    "MA 43200": ["MA 26500", "MA 41600"],
    "MA 45300": ["MA 26500"],
    "STAT 41700": ["STAT 35000", "MA 41600"],
    "STAT 51200": ["STAT 35000"],
    "FIN 41100": ["FIN 30400"],
    "FIN 41150": ["FIN 30400"],
    "FIN 41650": ["FIN 30400"],
}

COURSE_PRIORITY = [
    "CS 18200",
    "CS 24000",
    "FIN 30400",
    "MA 41600",
    "MA 36600",
    "CS 31400",
    "CS 25000",
    "CS 25100",
    "CS 25200",
    "CS 38100",
    "CS 37300",
    "CS 44800",
    "CS 47100",
    "CS 35400",
    "STAT 41700",
    "MA 43200",
    "FIN 41100",
    "FIN 41300",
    "FIN 41150",
    "FIN 41650",
    "MA 34100",
    "MA 42500",
    "MA 45300",
    "STAT 51200",
    "IE 33500",
]

TRACK_COMBINATIONS = [
    ("algorithms",),
    ("machine_intelligence",),
    ("cse",),
    ("algorithms", "machine_intelligence"),
    ("algorithms", "cse"),
    ("machine_intelligence", "cse"),
    ("algorithms", "machine_intelligence", "cse"),
]

EXTRA_COMBINATIONS = [
    (),
    ("math",),
    ("statistics",),
    ("finance",),
    ("math", "statistics"),
    ("math", "finance"),
    ("statistics", "finance"),
    ("math", "statistics", "finance"),
]


def term_index(term: str) -> int:
    return TERMS.index(term)


def credits_for(code: str, catalog: dict[str, Any]) -> float:
    record = catalog.get("courses", {}).get(code, {})
    credits = record.get("credits", {})
    if isinstance(credits, dict) and credits.get("max") is not None:
        return float(credits["max"])
    return 3.0


def title_for(code: str, catalog: dict[str, Any]) -> str:
    record = catalog.get("courses", {}).get(code, {})
    return record.get("title") or code


def ordered_unique(codes: list[str]) -> list[str]:
    seen = set()
    out = []
    priority = {code: index for index, code in enumerate(COURSE_PRIORITY)}
    for code in sorted([normalize_code(code) for code in codes], key=lambda item: priority.get(item, 999)):
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def profile_for(extra_ids: tuple[str, ...]) -> dict[str, Any]:
    programs = ["Computer Science-BS"]
    majors = ["Computer Science"]
    minors: list[str] = []
    fields = ["Computer Science"]
    overrides: list[str] = []

    if "math" in extra_ids:
        programs.append("Mathematics-BS")
        majors.append("Mathematics")
        fields.append("Mathematics")
    if "statistics" in extra_ids:
        programs.append("Statistics-BS")
        majors.append("Statistics")
        fields.append("Statistics")
    if "finance" in extra_ids:
        minors.append("Finance")
        fields.append("Finance")
        overrides.extend(FINANCE_OVERRIDES)

    return {
        "programs": programs,
        "majors": majors,
        "minors": minors,
        "fields_of_study": fields,
        "planned_restriction_overrides": overrides,
    }


def courses_for(track_ids: tuple[str, ...], extra_ids: tuple[str, ...]) -> list[str]:
    codes = list(CS_CORE_REMAINING)
    for track_id in track_ids:
        codes.extend(TRACKS[track_id]["courses"])
    for extra_id in extra_ids:
        codes.extend(EXTRAS[extra_id]["courses"])
    return ordered_unique(codes)


def is_available(code: str, completed_codes: set[str]) -> bool:
    return all(req in completed_codes for req in PREREQS.get(code, []))


def schedule_named_courses(
    codes: list[str],
    catalog: dict[str, Any],
    completed_codes: set[str],
    cap: int,
) -> list[dict[str, Any]]:
    unscheduled = list(codes)
    completed = set(completed_codes)
    semesters: list[dict[str, Any]] = []

    for term in TERMS:
        term_courses = []
        load = 0.0
        made_progress = True
        while made_progress:
            made_progress = False
            for code in list(unscheduled):
                course_credits = credits_for(code, catalog)
                if is_available(code, completed) and load + course_credits <= cap:
                    term_courses.append(course_row(code, term, catalog))
                    load += course_credits
                    unscheduled.remove(code)
                    made_progress = True
        if term_courses:
            semesters.append({"term": term, "credits": load, "courses": term_courses})
            completed.update(course["code"] for course in term_courses)
        if not unscheduled:
            break

    if unscheduled:
        raise RuntimeError(f"Could not schedule courses under cap {cap}: {', '.join(unscheduled)}")
    return semesters


def course_row(code: str, term: str, catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": code,
        "title": title_for(code, catalog),
        "credits": credits_for(code, catalog),
    }


def add_placeholder_credits(
    semesters: list[dict[str, Any]],
    placeholder_credits: float,
    cap: int,
) -> None:
    if placeholder_credits <= 0:
        return

    by_term = {semester["term"]: semester for semester in semesters}
    last_index = max(term_index(semester["term"]) for semester in semesters)
    for term in TERMS[: last_index + 1]:
        by_term.setdefault(term, {"term": term, "credits": 0.0, "courses": []})

    remaining = placeholder_credits
    serial = 1
    while remaining > 0:
        candidates = [
            semester
            for semester in by_term.values()
            if term_index(semester["term"]) <= last_index and semester["credits"] < cap
        ]
        if not candidates:
            raise RuntimeError("Not enough room to place placeholder credits before the target term")
        candidates.sort(key=lambda row: (row["credits"], term_index(row["term"])))
        semester = candidates[0]
        available = cap - semester["credits"]
        chunk = min(3.0, remaining, available)
        if chunk <= 0:
            break
        semester["courses"].append(
            {
                "code": f"PLACEHOLDER REMAINING-COS-CORE-ELECTIVE-{serial}",
                "title": "Remaining College of Science core / elective credit",
                "credits": chunk,
            }
        )
        semester["credits"] += chunk
        remaining -= chunk
        serial += 1

    semesters[:] = [by_term[term] for term in TERMS if term in by_term and by_term[term]["courses"]]


def schedule_loads(semesters: list[dict[str, Any]]) -> list[float]:
    return [float(semester["credits"]) for semester in semesters]


def load_spread(loads: list[float]) -> float:
    return max(loads) - min(loads) if loads else 0.0


def balance_objective(loads: list[float]) -> tuple[float, float, float, float]:
    """Prefer no term over 18, then the most even spread across the same terms."""
    if not loads:
        return (0.0, 0.0, 0.0, 0.0)
    average = sum(loads) / len(loads)
    max_load = max(loads)
    over_18 = max(0.0, max_load - 18.0)
    squared_error = sum((load - average) ** 2 for load in loads)
    return (over_18, load_spread(loads), squared_error, max_load)


def balanced_stats(semesters: list[dict[str, Any]]) -> dict[str, float]:
    loads = schedule_loads(semesters)
    return {
        "max_semester_credits": max(loads) if loads else 0.0,
        "min_semester_credits": min(loads) if loads else 0.0,
        "load_spread": load_spread(loads),
    }


def dependency_maps(rows: list[dict[str, Any]], completed_codes: set[str]) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    planned_by_code = {normalize_code(row["code"]): index for index, row in enumerate(rows)}
    prereqs: dict[int, set[int]] = {index: set() for index in range(len(rows))}
    dependents: dict[int, set[int]] = {index: set() for index in range(len(rows))}

    for index, row in enumerate(rows):
        code = normalize_code(row["code"])
        if code.startswith("PLACEHOLDER"):
            continue
        for req in PREREQS.get(code, []):
            req = normalize_code(req)
            if req in completed_codes:
                continue
            req_index = planned_by_code.get(req)
            if req_index is not None:
                prereqs[index].add(req_index)
                dependents[req_index].add(index)
    return prereqs, dependents


def valid_assignment(
    course_index: int,
    target_term: int,
    assignment: dict[int, int],
    prereqs: dict[int, set[int]],
    dependents: dict[int, set[int]],
) -> bool:
    return all(assignment[req] < target_term for req in prereqs[course_index]) and all(
        target_term < assignment[dependent] for dependent in dependents[course_index]
    )


def course_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    priority = {code: index for index, code in enumerate(COURSE_PRIORITY)}
    code = normalize_code(row["code"])
    return (priority.get(code, 999), code)


def balance_semesters(
    semesters: list[dict[str, Any]],
    completed_codes: set[str],
    cap: int,
) -> list[dict[str, Any]]:
    """Delay or pull forward flexible courses to even loads within the same target term."""
    if not semesters:
        return []

    target_index = term_index(semesters[-1]["term"])
    terms = TERMS[: target_index + 1]
    rows: list[dict[str, Any]] = []
    assignment: dict[int, int] = {}
    for semester in semesters:
        semester_index = term_index(semester["term"])
        for row in semester.get("courses", []):
            assignment[len(rows)] = semester_index
            rows.append(deepcopy(row))

    prereqs, dependents = dependency_maps(rows, completed_codes)
    loads = [0.0 for _ in terms]
    for index, row in enumerate(rows):
        loads[assignment[index]] += float(row.get("credits", 0))

    def candidate_objective(candidate_loads: list[float]) -> tuple[float, float, float, float]:
        return balance_objective(candidate_loads)

    def apply_single_move(index: int, target: int, candidate_loads: list[float]) -> None:
        source = assignment[index]
        credits = float(rows[index].get("credits", 0))
        candidate_loads[source] -= credits
        candidate_loads[target] += credits

    def apply_swap(left: int, right: int, candidate_loads: list[float]) -> None:
        left_term = assignment[left]
        right_term = assignment[right]
        left_credits = float(rows[left].get("credits", 0))
        right_credits = float(rows[right].get("credits", 0))
        candidate_loads[left_term] += right_credits - left_credits
        candidate_loads[right_term] += left_credits - right_credits

    improved = True
    while improved:
        improved = False
        best_objective = candidate_objective(loads)
        best_move: tuple[str, int, int] | None = None

        for index, row in enumerate(rows):
            source = assignment[index]
            credits = float(row.get("credits", 0))
            for target in range(len(terms)):
                if target == source:
                    continue
                if loads[target] + credits > cap:
                    continue
                if not valid_assignment(index, target, assignment, prereqs, dependents):
                    continue
                candidate_loads = list(loads)
                apply_single_move(index, target, candidate_loads)
                objective = candidate_objective(candidate_loads)
                if objective < best_objective:
                    best_objective = objective
                    best_move = ("move", index, target)

        if best_move:
            _, index, target = best_move
            apply_single_move(index, target, loads)
            assignment[index] = target
            improved = True
            continue

        for left in range(len(rows)):
            for right in range(left + 1, len(rows)):
                left_term = assignment[left]
                right_term = assignment[right]
                if left_term == right_term:
                    continue
                left_credits = float(rows[left].get("credits", 0))
                right_credits = float(rows[right].get("credits", 0))
                if loads[left_term] - left_credits + right_credits > cap:
                    continue
                if loads[right_term] - right_credits + left_credits > cap:
                    continue
                candidate_assignment = dict(assignment)
                candidate_assignment[left] = right_term
                candidate_assignment[right] = left_term
                if not valid_assignment(left, right_term, candidate_assignment, prereqs, dependents):
                    continue
                if not valid_assignment(right, left_term, candidate_assignment, prereqs, dependents):
                    continue
                candidate_loads = list(loads)
                apply_swap(left, right, candidate_loads)
                objective = candidate_objective(candidate_loads)
                if objective < best_objective:
                    best_objective = objective
                    best_move = ("swap", left, right)

        if best_move:
            _, left, right = best_move
            left_term = assignment[left]
            right_term = assignment[right]
            apply_swap(left, right, loads)
            assignment[left] = right_term
            assignment[right] = left_term
            improved = True

    by_term = {term: {"term": term, "credits": 0.0, "courses": []} for term in terms}
    for index, row in enumerate(rows):
        term = terms[assignment[index]]
        by_term[term]["courses"].append(row)
        by_term[term]["credits"] += float(row.get("credits", 0))

    for semester in by_term.values():
        semester["courses"].sort(key=course_sort_key)
    return [by_term[term] for term in terms]


def planned_instances(semesters: list[dict[str, Any]]) -> list[CourseInstance]:
    planned = []
    for semester in semesters:
        for row in semester["courses"]:
            planned.append(
                CourseInstance(
                    code=normalize_code(row["code"]),
                    title=row.get("title", ""),
                    credits=float(row.get("credits", 0)),
                    term=semester["term"],
                    source="Planned",
                    raw=row,
                )
            )
    return planned


def build_plan_dict(variant: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_graduation": variant["target_graduation"],
        "student_profile": deepcopy(variant["student_profile"]),
        "assumptions": [
            "Generated from local encoded requirements and local catalog data.",
            "No summer coursework is modeled in this generated plan.",
            "Exact College of Science core categories and official degree audit applicability still need advisor verification.",
        ]
        + variant.get("notes", []),
        "semesters": deepcopy(variant["semesters"]),
    }


def validate_variant(
    variant: dict[str, Any],
    completed: list[CourseInstance],
    catalog: dict[str, Any],
    requirements: dict[str, Any],
) -> dict[str, Any]:
    planned = planned_instances(variant["semesters"])
    all_courses = completed + planned
    credits = course_credit_lookup(all_courses, catalog)
    plan = build_plan_dict(variant)

    cs = audit_cs(requirements["cs"], all_courses)
    tracks_by_id = {track["id"]: track for track in cs["tracks"]}
    target_track_ids = [TRACKS[track_id]["audit_id"] for track_id in variant["cs_track_ids"]]
    target_tracks_ok = all(tracks_by_id[track_id]["ok"] for track_id in target_track_ids)
    satisfied_tracks = [track["label"] for track in cs["tracks"] if track["ok"]]

    math_ok = True
    finance_ok = True
    statistics_ok = True
    if "math" in variant["extra_ids"]:
        math_ok = audit_math(requirements["math"], all_courses, credits)["ok"]
    if "finance" in variant["extra_ids"]:
        finance_ok = audit_finance(requirements["finance"], all_courses)["ok"]
    if "statistics" in variant["extra_ids"]:
        statistics_ok = audit_statistics_math_emphasis(requirements["statistics"], all_courses, credits)["ok"]

    prereqs = audit_prerequisites(catalog, completed, planned)
    restrictions = audit_restrictions(catalog, completed, planned, plan)

    return {
        "target_tracks_ok": target_tracks_ok,
        "satisfied_cs_tracks": satisfied_tracks,
        "math_ok": math_ok,
        "statistics_ok": statistics_ok,
        "finance_ok": finance_ok,
        "prerequisites_ok": prereqs["ok"],
        "restriction_checks_ok": restrictions["ok"],
        "warnings": prereqs["warnings"] + restrictions["warnings"],
    }


def build_variant(
    track_ids: tuple[str, ...],
    extra_ids: tuple[str, ...],
    cap: int,
    completed: list[CourseInstance],
    catalog: dict[str, Any],
    requirements: dict[str, Any],
) -> dict[str, Any]:
    completed_codes = {course.code for course in completed}
    completed_credits = sum(course.credits for course in completed)
    named_codes = courses_for(track_ids, extra_ids)
    named_credits = sum(credits_for(code, catalog) for code in named_codes)
    placeholder_credits = max(0.0, DEGREE_TARGET_CREDITS - completed_credits - named_credits)
    semesters = schedule_named_courses(named_codes, catalog, completed_codes, cap)
    add_placeholder_credits(semesters, placeholder_credits, cap)

    target = semesters[-1]["term"]
    max_load = max(semester["credits"] for semester in semesters)
    planned_credits = sum(semester["credits"] for semester in semesters)
    track_labels = [TRACKS[track_id]["label"] for track_id in track_ids]
    extra_labels = [EXTRAS[extra_id]["label"] for extra_id in extra_ids]
    notes = []
    for track_id in track_ids:
        notes.extend(TRACKS[track_id].get("notes", []))
    for extra_id in extra_ids:
        notes.extend(EXTRAS[extra_id].get("notes", []))

    variant = {
        "id": variant_id(track_ids, extra_ids),
        "cs_track_ids": list(track_ids),
        "cs_tracks": track_labels,
        "extra_ids": list(extra_ids),
        "extras": extra_labels,
        "target_graduation": target,
        "semester_cap": cap,
        "max_semester_credits": max_load,
        "completed_credits_listed": completed_credits,
        "named_planned_credits": named_credits,
        "placeholder_credits": placeholder_credits,
        "planned_credits": planned_credits,
        "total_if_all_count": completed_credits + planned_credits,
        "student_profile": profile_for(extra_ids),
        "notes": notes,
        "semesters": semesters,
    }
    variant["validation"] = validate_variant(variant, completed, catalog, requirements)
    balanced_semesters = balance_semesters(semesters, completed_codes, cap)
    variant["balanced_semesters"] = balanced_semesters
    variant["balanced"] = balanced_stats(balanced_semesters)
    balanced_variant = dict(variant)
    balanced_variant["semesters"] = balanced_semesters
    variant["balanced_validation"] = validate_variant(balanced_variant, completed, catalog, requirements)
    return variant


def variant_id(track_ids: tuple[str, ...], extra_ids: tuple[str, ...]) -> str:
    track_part = "_".join(track_ids)
    extra_part = "_".join(extra_ids)
    return "cs_" + track_part + (f"_{extra_part}" if extra_part else "")


def target_is_earlier(left: str, right: str) -> bool:
    return term_index(left) < term_index(right)


def fmt_credits(value: float) -> str:
    return str(int(value)) if abs(value - int(value)) < 0.001 else f"{value:.1f}"


def format_semesters(semesters: list[dict[str, Any]]) -> str:
    lines = []
    for semester in semesters:
        course_bits = [f"{row['code']} ({fmt_credits(row['credits'])})" for row in semester["courses"]]
        lines.append(f"- {semester['term']} - {fmt_credits(semester['credits'])} cr: " + ", ".join(course_bits))
    return "\n".join(lines)


def format_schedule(variant: dict[str, Any], key: str = "semesters") -> str:
    return format_semesters(variant[key])


def format_loads(semesters: list[dict[str, Any]]) -> str:
    return ", ".join(f"{semester['term']} {fmt_credits(semester['credits'])}" for semester in semesters)


def make_report(data: dict[str, Any]) -> str:
    variants = data["variants"]
    by_id = {variant["id"]: variant for variant in variants}
    key_ids = [
        "cs_algorithms",
        "cs_algorithms_math",
        "cs_algorithms_machine_intelligence_cse_math",
        "cs_algorithms_statistics",
        "cs_algorithms_math_statistics",
        "cs_algorithms_math_finance",
        "cs_algorithms_math_statistics_finance",
        "cs_algorithms_machine_intelligence_cse_math_statistics_finance",
    ]

    lines = [
        "# Fastest Graduation Plan Variants",
        "",
        f"Generated: {data['generated_at']}",
        "",
        "Assumptions:",
        "- Fall/spring only; summer offerings are not modeled.",
        "- Preferred cap is 20 credits. A separate 21-credit check is included when it improves graduation timing.",
        "- Plans use the local encoded requirements and catalog parser. Official degree audit, core category mapping, registration permits, and actual section availability still need advising verification.",
        "- Each variant keeps the fastest schedule and a balanced same-target schedule; the XLSX renders the balanced versions on a separate Balanced Plans sheet.",
        "",
        "Bottom line:",
        "- CS Algorithms only: Spring 2028 under the preferred cap.",
        "- CS Algorithms + Math, Statistics, Finance, or their combinations: Spring 2028 under the preferred cap.",
        "- All listed combinations also fit Spring 2028 under the preferred cap except the heaviest all-tracks + Math + Statistics + Finance case, which is Fall 2028 at <=20 credits or Spring 2028 with one 21-credit term.",
        "- Summer acceleration: if CS 25200 is actually offered/approved in Summer 2027, the CS Algorithms-only plan could move to Fall 2027 by taking CS 35400 in Fall 2027. Summer offerings are not verified by this local data.",
        "",
        "## Summary Table",
        "",
        "| Variant | CS tracks | Add-ons | <=20 graduation | Fast max | Balanced max | Balanced spread | Planned cr | Placeholder cr | <=21 faster option |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]

    for variant in variants:
        accelerated = variant.get("overload_21")
        accelerated_text = ""
        if accelerated and target_is_earlier(accelerated["target_graduation"], variant["target_graduation"]):
            accelerated_text = f"{accelerated['target_graduation']} at max {fmt_credits(accelerated['max_semester_credits'])}"
        lines.append(
            "| {id} | {tracks} | {extras} | {target} | {max_load} | {balanced_max} | {balanced_spread} | {planned} | {placeholders} | {accelerated} |".format(
                id=variant["id"],
                tracks=", ".join(variant["cs_tracks"]),
                extras=", ".join(variant["extras"]) or "none",
                target=variant["target_graduation"],
                max_load=fmt_credits(variant["max_semester_credits"]),
                balanced_max=fmt_credits(variant["balanced"]["max_semester_credits"]),
                balanced_spread=fmt_credits(variant["balanced"]["load_spread"]),
                planned=fmt_credits(variant["planned_credits"]),
                placeholders=fmt_credits(variant["placeholder_credits"]),
                accelerated=accelerated_text,
            )
        )

    lines.extend(["", "## Key Schedules", ""])
    for key_id in key_ids:
        variant = by_id.get(key_id)
        if not variant:
            continue
        lines.extend(
            [
                f"### {key_id}",
                "",
                f"Target: {variant['target_graduation']} at max {fmt_credits(variant['max_semester_credits'])} credits.",
                "",
                format_schedule(variant),
                "",
                f"Balanced same-target loads: {format_loads(variant['balanced_semesters'])}.",
                "",
            ]
        )
        accelerated = variant.get("overload_21")
        if accelerated and target_is_earlier(accelerated["target_graduation"], variant["target_graduation"]):
            lines.extend(
                [
                    f"21-credit option: {accelerated['target_graduation']} at max {fmt_credits(accelerated['max_semester_credits'])} credits.",
                    "",
                    format_schedule(accelerated),
                    "",
                ]
            )

    lines.extend(
        [
            "## Validation",
            "",
            "Every generated <=20 variant and balanced same-target schedule passed the local prerequisite, restriction, target CS track, and selected add-on requirement checks encoded in this repo.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    completed = load_completed(ROOT / "completed_courses.json")
    catalog = load_json(ROOT / "course_catalog.json")
    requirements = {
        "cs": load_json(ROOT / "requirements" / "cs.json"),
        "math": load_json(ROOT / "requirements" / "math.json"),
        "finance": load_json(ROOT / "requirements" / "finance_minor.json"),
        "statistics": load_json(ROOT / "requirements" / "statistics_math_emphasis.json"),
    }

    variants = []
    for track_ids in TRACK_COMBINATIONS:
        for extra_ids in EXTRA_COMBINATIONS:
            variant = build_variant(track_ids, extra_ids, PREFERRED_CAP, completed, catalog, requirements)
            overload = build_variant(track_ids, extra_ids, OVERLOAD_CAP, completed, catalog, requirements)
            variant["overload_21"] = {
                key: overload[key]
                for key in [
                    "target_graduation",
                    "semester_cap",
                    "max_semester_credits",
                    "planned_credits",
                    "placeholder_credits",
                    "semesters",
                    "balanced_semesters",
                    "balanced",
                    "validation",
                    "balanced_validation",
                ]
            }
            variants.append(variant)

    failed = [
        variant["id"]
        for variant in variants
        if not (
            variant["validation"]["target_tracks_ok"]
            and variant["validation"]["math_ok"]
            and variant["validation"]["statistics_ok"]
            and variant["validation"]["finance_ok"]
            and variant["validation"]["prerequisites_ok"]
            and variant["validation"]["restriction_checks_ok"]
        )
    ]
    if failed:
        raise RuntimeError("Generated variants failed validation: " + ", ".join(failed))

    data = {
        "generated_at": date.today().isoformat(),
        "assumptions": [
            "Fall/spring only; summer offerings are not modeled.",
            "Preferred cap is 20 credits; 21-credit alternatives are checked separately.",
            "Placeholder credits represent remaining College of Science core, gen ed, or elective credit needed to reach 120 listed credits.",
        ],
        "completed_credits_listed": sum(course.credits for course in completed),
        "preferred_semester_cap": PREFERRED_CAP,
        "overload_semester_cap": OVERLOAD_CAP,
        "variants": variants,
    }

    (ROOT / "plan_variants.yaml").write_text(yaml.safe_dump(data, sort_keys=False, width=140), encoding="utf-8")
    (ROOT / "plan_variants_report.md").write_text(make_report(data), encoding="utf-8")

    baseline = next(variant for variant in variants if variant["id"] == "cs_algorithms")
    (ROOT / "plan_cs_algorithms.yaml").write_text(yaml.safe_dump(build_plan_dict(baseline), sort_keys=False, width=120), encoding="utf-8")

    print(json.dumps({"variants": len(variants), "failed": failed, "baseline_target": baseline["target_graduation"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
