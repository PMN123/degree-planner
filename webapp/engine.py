#!/usr/bin/env python3
"""Generic, major-agnostic degree-requirement engine.

Unlike the hand-written ``audit_plan.py`` (which encodes the CS/Math/Stats/Finance
rules directly in Python), this module evaluates *any* program described by a
declarative JSON schema. A program is a tree of requirement nodes; each node is
evaluated against the set of courses a student has completed or planned.

This is what makes the web app work for "any major / any plan": to support a new
program you add one JSON file under ``webapp/programs/`` — no code changes.

Requirement node schema
------------------------
Every node has a ``kind`` and a human ``name``. Supported kinds::

    all_of        every listed course / child node must be satisfied
    one_of        at least one listed course / child node must be satisfied
    choose        pick N (``choose``) or N credits (``choose_credits``) of options
    credits       accumulate ``credits`` from courses matching ``match`` filter
    track_select  pick ``choose`` (default 1) of ``tracks`` (each an all_of-style node)

Leaf courses are given as ``courses: ["CS 18000", ...]``. Nested structure uses
``children`` (for all_of/one_of) or ``options`` / ``tracks`` (for choose/track_select).
An option may itself be a node (e.g. a two-course combo ``CS 31100 + CS 41100``).

The evaluator never mutates its inputs and is deterministic, so it is safe to call
once per request from the web server.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Course-code normalisation (kept identical to audit_plan.normalize_code so the
# generic engine and the legacy auditor agree on what "CS 18000" means).
# ---------------------------------------------------------------------------


def normalize_code(value: str) -> str:
    value = " ".join(str(value).strip().upper().replace("-", " ").split())
    match = re.match(r"^([A-Z]+)\s*([0-9][0-9A-Z]{2,5})$", value)
    if match:
        subject, number = match.groups()
        if number.isdigit() and len(number) < 5:
            number = number.zfill(5)
        return f"{subject} {number}"
    return value


def code_subject(code: str) -> str:
    return code.split()[0] if " " in code else code


def code_number(code: str) -> int | None:
    match = re.search(r"\b([0-9]{3,5})\b", code)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class Context:
    """Read-only view of what the student has, shared across one evaluation."""

    def __init__(self, available: set[str], credits: dict[str, float]):
        self.available = available
        self.credits = credits

    def has(self, code: str) -> bool:
        return normalize_code(code) in self.available

    def credit(self, code: str) -> float:
        return float(self.credits.get(normalize_code(code), 3.0))


def _course_result(code: str, ctx: Context) -> dict[str, Any]:
    code = normalize_code(code)
    return {
        "type": "course",
        "code": code,
        "satisfied": code in ctx.available,
        "credits": ctx.credit(code),
    }


def _option_satisfied(option: Any, ctx: Context) -> tuple[bool, list[str], dict[str, Any]]:
    """Evaluate a single ``choose`` option.

    Returns (satisfied, used_course_codes, detail). An option is either a course
    code string or a nested requirement node (e.g. an all_of combo).
    """
    if isinstance(option, str):
        code = normalize_code(option)
        sat = code in ctx.available
        return sat, ([code] if sat else []), {
            "type": "course",
            "code": code,
            "satisfied": sat,
            "credits": ctx.credit(code),
        }
    detail = evaluate_node(option, ctx)
    return detail["satisfied"], list(detail.get("used_courses", [])), detail


def evaluate_node(node: dict[str, Any], ctx: Context) -> dict[str, Any]:
    kind = node.get("kind", "all_of")
    name = node.get("name", node.get("label", ""))
    base = {"id": node.get("id"), "name": name, "kind": kind}
    if node.get("note"):
        base["note"] = node["note"]

    # ---- all_of: every course and child must be satisfied --------------------
    if kind in ("all_of", "group"):
        children: list[dict[str, Any]] = []
        used: list[str] = []
        for code in node.get("courses", []):
            res = _course_result(code, ctx)
            children.append(res)
            if res["satisfied"]:
                used.append(res["code"])
        for child in node.get("children", []):
            res = evaluate_node(child, ctx)
            children.append(res)
            used.extend(res.get("used_courses", []))
        total = len(children)
        have = sum(1 for c in children if c["satisfied"])
        return {
            **base,
            "satisfied": have == total and total > 0,
            "children": children,
            "used_courses": _dedupe(used),
            "progress": {"have": have, "need": total, "unit": "items"},
        }

    # ---- one_of: at least one option satisfied -------------------------------
    if kind == "one_of":
        children = []
        used = []
        satisfied = False
        for code in node.get("courses", []):
            res = _course_result(code, ctx)
            children.append(res)
            if res["satisfied"]:
                satisfied = True
                used.append(res["code"])
        for child in node.get("children", []):
            res = evaluate_node(child, ctx)
            children.append(res)
            if res["satisfied"]:
                satisfied = True
                used.extend(res.get("used_courses", []))
        return {
            **base,
            "satisfied": satisfied,
            "children": children,
            "used_courses": _dedupe(used),
            "progress": {"have": 1 if satisfied else 0, "need": 1, "unit": "option"},
        }

    # ---- choose: N options or N credits --------------------------------------
    if kind == "choose":
        options = node.get("options", node.get("courses", []))
        want_credits = node.get("choose_credits")
        want_count = node.get("choose", 1 if want_credits is None else None)
        max_by_subject = (node.get("constraints") or {}).get("max_by_subject", {})

        evaluated = []
        for opt in options:
            sat, used_codes, detail = _option_satisfied(opt, ctx)
            evaluated.append({"satisfied": sat, "used": used_codes, "detail": detail})

        # Greedily accept satisfied options, honouring per-subject caps.
        subject_used: dict[str, int] = {}
        chosen_credits = 0.0
        chosen_count = 0
        chosen_courses: list[str] = []
        for ev in evaluated:
            if not ev["satisfied"]:
                continue
            subj = code_subject(ev["used"][0]) if ev["used"] else None
            cap = max_by_subject.get(subj) if subj else None
            if cap is not None and subject_used.get(subj, 0) >= cap:
                ev["detail"]["capped"] = True
                continue
            if subj is not None:
                subject_used[subj] = subject_used.get(subj, 0) + 1
            chosen_count += 1
            chosen_courses.extend(ev["used"])
            chosen_credits += sum(ctx.credit(c) for c in ev["used"])
            ev["detail"]["counted"] = True

        if want_credits is not None:
            satisfied = chosen_credits >= want_credits
            progress = {"have": round(chosen_credits, 1), "need": want_credits, "unit": "credits"}
        else:
            satisfied = chosen_count >= (want_count or 0)
            progress = {"have": chosen_count, "need": want_count or 0, "unit": "courses"}

        return {
            **base,
            "satisfied": satisfied,
            "children": [ev["detail"] for ev in evaluated],
            "used_courses": _dedupe(chosen_courses),
            "progress": progress,
            "constraints": node.get("constraints"),
        }

    # ---- credits: accumulate from courses matching a filter ------------------
    if kind == "credits":
        need = float(node.get("credits", 0))
        match = node.get("match") or {}
        explicit = {normalize_code(c) for c in node.get("courses", [])}
        if node.get("placeholder"):
            # Cannot be auto-satisfied from the course list (e.g. gen-ed buckets).
            return {
                **base,
                "satisfied": False,
                "placeholder": True,
                "used_courses": [],
                "progress": {"have": 0, "need": need, "unit": "credits"},
            }
        got = 0.0
        used = []
        for code in sorted(ctx.available):
            if explicit and code not in explicit:
                if not _matches_filter(code, match):
                    continue
            elif not explicit and not _matches_filter(code, match):
                continue
            got += ctx.credit(code)
            used.append(code)
        return {
            **base,
            "satisfied": got >= need and need > 0,
            "used_courses": used,
            "progress": {"have": round(got, 1), "need": need, "unit": "credits"},
        }

    # ---- track_select: choose N tracks (each an all_of-style node) -----------
    if kind == "track_select":
        want = node.get("choose", 1)
        tracks = []
        satisfied_tracks = 0
        used = []
        for track in node.get("tracks", []):
            res = evaluate_node({**track, "kind": track.get("kind", "all_of")}, ctx)
            tracks.append(res)
            if res["satisfied"]:
                satisfied_tracks += 1
                used.extend(res.get("used_courses", []))
        return {
            **base,
            "satisfied": satisfied_tracks >= want,
            "tracks": tracks,
            "used_courses": _dedupe(used),
            "progress": {"have": satisfied_tracks, "need": want, "unit": "tracks"},
        }

    # ---- unknown kind --------------------------------------------------------
    return {**base, "satisfied": False, "error": f"unknown kind: {kind}", "used_courses": []}


def _matches_filter(code: str, match: dict[str, Any]) -> bool:
    if not match:
        return False
    subj = code_subject(code)
    num = code_number(code)
    if "subjects" in match and subj not in match["subjects"]:
        return False
    if "min_number" in match and (num is None or num < match["min_number"]):
        return False
    if "max_number" in match and (num is None or num > match["max_number"]):
        return False
    return True


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for it in items:
        seen.setdefault(it, None)
    return list(seen.keys())


def _progress(result: dict[str, Any]) -> tuple[float, float]:
    """(have, need) in "atomic requirement" units, used for the headline percent.

    Each required course counts as one unit, each one_of / credit-bucket as one,
    a choose-N as N, and a track_select as the most-progressed track (you only
    need one). Credit buckets contribute a single fractionally-filled unit so a
    big gen-ed placeholder doesn't swamp the percentage.
    """
    node_type = result.get("type")
    kind = result.get("kind")
    if node_type == "course":
        return (1.0 if result["satisfied"] else 0.0, 1.0)
    if kind in ("all_of", "group"):
        have = need = 0.0
        for child in result.get("children", []):
            h, n = _progress(child)
            have += h
            need += n
        return (have, need) if need else (1.0 if result.get("satisfied") else 0.0, 1.0)
    if kind == "one_of":
        return (1.0 if result.get("satisfied") else 0.0, 1.0)
    if kind == "choose":
        prog = result.get("progress", {})
        need = float(prog.get("need") or 0)
        have = float(prog.get("have") or 0)
        if prog.get("unit") == "credits":
            return (min(have / need, 1.0) if need else 0.0, 1.0)
        return (min(have, need), need if need else 1.0)
    if kind == "credits":
        prog = result.get("progress", {})
        need = float(prog.get("need") or 0)
        have = float(prog.get("have") or 0)
        return (min(have / need, 1.0) if need else (1.0 if result.get("satisfied") else 0.0), 1.0)
    if kind == "track_select":
        tracks = result.get("tracks", [])
        if not tracks:
            return (1.0 if result.get("satisfied") else 0.0, 1.0)
        return max((_progress(tr) for tr in tracks), key=lambda hn: (hn[0] / hn[1] if hn[1] else 0))
    return (1.0 if result.get("satisfied") else 0.0, 1.0)


def evaluate_program(
    program: dict[str, Any],
    available: set[str],
    credits: dict[str, float],
) -> dict[str, Any]:
    """Evaluate a full program. Returns a serialisable result tree."""
    ctx = Context({normalize_code(c) for c in available}, credits)
    requirement_results = [evaluate_node(req, ctx) for req in program.get("requirements", [])]

    used_courses: list[str] = []
    for res in requirement_results:
        used_courses.extend(res.get("used_courses", []))

    sat_total = tot_total = 0.0
    for res in requirement_results:
        s, t = _progress(res)
        sat_total += s
        tot_total += t

    return {
        "id": program.get("id"),
        "name": program.get("name"),
        "degree": program.get("degree"),
        "type": program.get("type", "major"),
        "college": program.get("college"),
        "catalog_year": program.get("catalog_year"),
        "total_credits": program.get("total_credits"),
        "source_url": program.get("source_url"),
        "notes": program.get("notes", []),
        "satisfied": all(r["satisfied"] for r in requirement_results) if requirement_results else False,
        "requirements": requirement_results,
        "used_courses": _dedupe(used_courses),
        "progress": {
            "have": round(sat_total, 1),
            "need": round(tot_total, 1),
            "unit": "requirements",
            "percent": round(100 * sat_total / tot_total) if tot_total else 0,
        },
    }
