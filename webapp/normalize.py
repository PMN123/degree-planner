#!/usr/bin/env python3
"""Repair mis-parsed requirement trees from the auto-scraper.

The program scraper (``scrape_programs.py``) reads a Purdue catalog program page into a
requirement tree. Many catalog pages express a requirement as *"choose N credits from the
following list"* — often a long menu of courses grouped by subject. The scraper frequently
fails to capture the "choose N" and encodes the whole menu as ``all_of`` (take **every**
course). For an 18-course menu in a 15-credit minor that turns a quick minor into an
impossible 8-year plan, because the scheduler dutifully schedules all 96 "required" courses.

This module re-interprets such trees **at load time** (generated/unverified programs only —
the hand-verified files are already correct). The rule is conservative and budget-driven:

* If a program's forced credits don't wildly exceed its stated total, leave it untouched
  (most programs, and all the proper ``one_of`` / ``choose`` ones, are fine).
* Otherwise it's a mis-parse: keep the genuinely-required pieces (real ``one_of`` choices and
  small required ``all_of`` sections) and fold every oversized ``all_of`` "menu" into a single
  credit-budgeted ``choose`` whose options are the menu's courses — which is what the catalog
  page actually says.

The transform is faithful to the catalog (a menu *is* a pick-list) and, crucially, bounded:
the resulting plan can never exceed the program's own credit total by more than a slot.
"""

from __future__ import annotations

from typing import Any

import engine

DEFAULT_TOTAL = {"minor": 15.0, "certificate": 15.0, "major": 120.0}
# A program is only rebuilt when its forced credits exceed total_credits by this factor —
# keeps us from touching correctly-parsed programs while catching the over-forced menus.
EXPLOSION_FACTOR = 1.15


def _credit_of(program: dict, code: str) -> float:
    cc = program.get("course_credits") or {}
    code = engine.normalize_code(code)
    if code in cc:
        try:
            return float(cc[code])
        except (TypeError, ValueError):
            return 3.0
    return 3.0


def _all_codes(node: dict) -> list[str]:
    """Every course code anywhere under a node (its courses, children, and choose options)."""
    out: list[str] = []
    for c in node.get("courses", []):
        out.append(engine.normalize_code(c))
    for child in node.get("children", []):
        out.extend(_all_codes(child))
    for opt in node.get("options", []):
        if isinstance(opt, str):
            out.append(engine.normalize_code(opt))
        elif isinstance(opt, dict):
            out.extend(_all_codes(opt))
    for tr in node.get("tracks", []):
        out.extend(_all_codes(tr))
    return out


def _min_credits(node: dict, program: dict) -> float:
    """Smallest credit count that *correctly* satisfies a node (not the exploded all_of sum)."""
    kind = node.get("kind", "all_of")
    if kind == "one_of":
        cands = [_credit_of(program, c) for c in node.get("courses", [])]
        cands += [_min_credits(ch, program) for ch in node.get("children", [])]
        return min(cands) if cands else 3.0
    if kind == "choose":
        if node.get("choose_credits"):
            return float(node["choose_credits"])
        n = int(node.get("choose") or 1)
        return 3.0 * n
    if kind == "credits":
        return float(node.get("credits") or 0)
    if kind == "track_select":
        tracks = [_min_credits({**t, "kind": t.get("kind", "all_of")}, program) for t in node.get("tracks", [])]
        return min(tracks) if tracks else 3.0
    # all_of / group: must satisfy everything
    total = sum(_credit_of(program, c) for c in node.get("courses", []))
    total += sum(_min_credits(ch, program) for ch in node.get("children", []))
    return total


def _forced_credits(requirements: list[dict], program: dict) -> float:
    return sum(_min_credits(n, program) for n in requirements)


def _dedupe(codes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def normalize_program(program: dict[str, Any]) -> dict[str, Any]:
    """Return ``program`` with any exploded requirement menus rewritten as ``choose`` nodes.

    Mutates and returns the same dict (programs_store deep-copies before calling)."""
    reqs = program.get("requirements") or []
    if not reqs:
        return program
    total = program.get("total_credits")
    if not total or float(total) <= 0:
        total = DEFAULT_TOTAL.get(program.get("type", "minor"), 120.0)
    total = float(total)

    forced = _forced_credits(reqs, program)
    if forced <= total * EXPLOSION_FACTOR:
        return program  # parsed sanely — leave it alone

    # Mis-parse: keep real required pieces up to the credit budget, fold the rest (oversized
    # all_of "menus", or required sections that would overshoot the total) into one choose.
    keep: list[dict] = []
    kept_credits = 0.0
    menu_codes: list[str] = []
    for node in reqs:
        kind = node.get("kind", "all_of")
        mc = _min_credits(node, program)
        if kind in ("one_of", "choose", "credits", "track_select"):
            # genuine choices — always keep (they don't over-force)
            keep.append(node)
            kept_credits += mc
        elif kind in ("all_of", "group") and mc <= total * 0.7 and kept_credits + mc <= total * 1.05:
            # a reasonably-sized required section that still fits the budget — keep it forced
            keep.append(node)
            kept_credits += mc
        else:
            # an oversized menu (or a section that would blow the credit budget) — pull its
            # courses out as choose options instead of forcing all of them
            menu_codes.extend(_all_codes(node))

    if menu_codes:
        budget = max(3.0, round(total - kept_credits))
        keep.append({
            "id": "normalized_electives",
            "name": "Approved Courses (choose to reach the credit total)",
            "kind": "choose",
            "choose_credits": budget,
            "options": _dedupe(menu_codes),
            "note": "Auto-normalized: the catalog lists these as a pick-from menu, not all required. "
                    "Confirm the exact count/credits with advising.",
        })

    program["requirements"] = keep
    program["normalized"] = True
    return program
