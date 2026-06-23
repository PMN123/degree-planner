#!/usr/bin/env python3
"""Build a starting term-by-term plan ("scaffold") for one or more programs.

Two strategies, picked automatically:

* **official** — if a program publishes a "Sample 4-Year Plan" (most Purdue majors do),
  use it as the backbone: map its Fall/Spring terms onto real calendar terms, drop
  already-completed courses, collapse inline alternatives, and turn named selectives
  ("Calculus Selective", "Elective") into fillable *slots*. The sample plan is then
  *reconciled* against the requirement tree so nothing it glosses over (a concentration,
  a multi-course selective) is silently missing.
* **solver** — otherwise (e.g. a minor, or a generated program without a sample plan),
  read the requirement tree directly: forced courses, multi-course ``one_of`` picks
  (placed as a default + swappable alternatives), ``choose`` selectives (one fillable
  slot per pick, restricted to the approved options), gen-ed credit buckets (open
  slots), and ``track_select`` concentrations (a single "pick a track" chooser slot
  that expands into that track's constrained slots on the board).

Additional selected programs (a second major, minors) are overlaid: their forced
courses and selective/concentration slots that aren't already present are bin-packed
into terms with room. Open gen-ed buckets are taken from the primary program only, so
three majors don't each contribute a duplicate "University Core" pile.

Every slot carries the exact set of courses that may fill it (``options``), so the UI
can restrict the picker instead of letting any catalog course satisfy a requirement.

The result is always *editable* — this is the starting point, not the only plan.
"""

from __future__ import annotations

import math
import re
from typing import Any

import engine
import equivalence

SEASON_NEXT = {"Fall": ("Spring", 1), "Spring": ("Fall", 0), "Summer": ("Fall", 0)}
DEFAULT_MAX_CREDITS = 16.0
SUMMER_MAX_CREDITS = 9.0  # Purdue summer terms are short — about half a normal load
OPEN_SLOT_CHUNK = 3.0  # split big gen-ed credit buckets into slots of this many credits


# ---------------------------------------------------------------------------
# Term helpers
# ---------------------------------------------------------------------------


def parse_term(term: str) -> tuple[str, int]:
    m = re.match(r"(Fall|Spring|Summer)\s+(\d{4})", term or "", re.I)
    if not m:
        return ("Fall", 2026)
    return (m.group(1).capitalize(), int(m.group(2)))


def term_sequence(start: str, count: int, use_summers: bool = False) -> list[str]:
    season, year = parse_term(start)
    out: list[str] = []
    for _ in range(count):
        out.append(f"{season} {year}")
        if season == "Fall":
            season, year = "Spring", year + 1
        elif season == "Spring":
            season, year = ("Summer", year) if use_summers else ("Fall", year)
        else:  # Summer
            season, year = "Fall", year
    return out


# ---------------------------------------------------------------------------
# Program introspection
# ---------------------------------------------------------------------------


def _walk(nodes: list[dict]):
    """Walk requirement nodes, descending into ``children`` (NOT into ``tracks`` — a
    ``track_select`` only requires one track, so its courses aren't flat-out required)."""
    for n in nodes:
        yield n
        yield from _walk(n.get("children", []))


def _walk_all(nodes: list[dict]):
    """Walk every node, descending into both ``children`` and concentration ``tracks``."""
    for n in nodes:
        yield n
        yield from _walk_all(n.get("children", []))
        yield from _walk_all(n.get("tracks", []))


def required_codes(program: dict) -> set[str]:
    """Codes that are flat-out required: ``all_of`` courses + single-course ``one_of`` picks."""
    out: set[str] = set()
    for n in _walk(program.get("requirements", [])):
        kind = n.get("kind")
        if kind in ("all_of", "group"):
            for c in n.get("courses", []):
                out.add(engine.normalize_code(c))
        elif kind == "one_of":
            cs = n.get("courses", [])
            if len(cs) == 1 and not n.get("children"):
                out.add(engine.normalize_code(cs[0]))
    return out


def countable_codes(program: dict) -> set[str]:
    """Every code that *could* count toward this program (forced, one_of, choose options,
    track options). Used to flag cross-program double-counting on the board."""
    out: set[str] = set()
    for n in _walk_all(program.get("requirements", [])):
        for c in n.get("courses", []):
            out.add(engine.normalize_code(c))
        for code in _flatten_option_codes(n.get("options", [])):
            out.add(code)
    return out


def credit_of(program: dict, code: str, catalog_credits: dict[str, float]) -> float:
    code = engine.normalize_code(code)
    cc = program.get("course_credits") or {}
    if code in cc:
        return float(cc[code])
    return float(catalog_credits.get(code, 3.0))


def _flatten_option_codes(options: list[Any]) -> list[str]:
    """Approved course codes for a ``choose``/``one_of`` node. A combo option (a nested
    node like ``CS 31100 + CS 41100``) is flattened to its individual courses — the
    audit still evaluates the combo correctly; this only feeds the slot picker."""
    out: list[str] = []
    for o in options:
        if isinstance(o, str):
            out.append(engine.normalize_code(o))
        elif isinstance(o, dict):
            for c in o.get("courses", []):
                out.append(engine.normalize_code(c))
            out.extend(_flatten_option_codes(o.get("options", [])))
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


def _constraint_note(node: dict) -> str | None:
    cons = node.get("constraints") or {}
    caps = cons.get("max_by_subject") or {}
    if caps:
        return "across all picks: " + ", ".join(f"max {v} {k}" for k, v in caps.items())
    return None


# ---------------------------------------------------------------------------
# Requirement tree -> plan cards (courses + constrained slots)
# ---------------------------------------------------------------------------


def _course_card(program: dict, code: str, cc: dict[str, float], *, locked: bool = True) -> dict:
    return {
        "code": engine.normalize_code(code), "title": "",
        "credits": credit_of(program, code, cc),
        "satisfies": [program["id"]], "locked": locked,
    }


def _choice_slot(program: dict, name: str, options: list[str], node: dict,
                 *, index: int = 0, total: int = 1, credits: float = 3.0) -> dict:
    label = name or "Selective"
    if total > 1:
        label = f"{label} — pick {index + 1} of {total}"
    return {
        "slot": True, "slotKind": "choose", "label": label,
        "credits": float(credits), "satisfies": [program["id"]],
        "options": options, "note": _constraint_note(node),
    }


def _open_slots(program: dict, name: str, total_credits: float, node: dict) -> list[dict]:
    """Split a gen-ed / placeholder credit bucket into small open slots so it distributes
    across terms instead of landing as one giant unmovable block. These stay unconstrained
    (any course) — narrowing them to approved lists needs catalog data we don't scrape yet."""
    out: list[dict] = []
    remaining = float(total_credits or 0)
    if remaining <= 0:
        return out
    while remaining > 0:
        chunk = min(OPEN_SLOT_CHUNK, remaining)
        out.append({
            "slot": True, "slotKind": "open", "label": name or "Elective",
            "credits": float(chunk), "satisfies": [program["id"]],
            "options": [], "note": node.get("note"),
            "match": node.get("match"),
        })
        remaining -= chunk
    return out


def _track_slot(program: dict, node: dict, cc: dict[str, float], completed_set: set[str]) -> dict:
    """A ``track_select`` concentration -> one 'pick a track' chooser slot. Each track
    carries its own expansion (forced courses + constrained selective slots) so the board
    can swap the chooser for the chosen track's cards in place."""
    tracks: list[dict] = []
    for t in node.get("tracks", []):
        children = t.get("children")
        if children:
            items = _collect(children, program, cc, completed_set)
        else:  # a track that is itself a flat course list
            items = _collect([{**t, "kind": t.get("kind", "all_of")}], program, cc, completed_set)
        tracks.append({"id": t.get("id"), "name": t.get("name", "Track"), "items": items})

    est = max((_cards_credits(tr["items"]) for tr in tracks), default=3.0)
    # The chooser is a placeholder that expands into many cards once a track is picked, so
    # it bin-packs with a small footprint (`credits`); `track_credits` is the real estimate
    # the UI can show and the client uses to distribute the expansion across terms.
    return {
        "slot": True, "slotKind": "track", "label": node.get("name", "Concentration"),
        "credits": OPEN_SLOT_CHUNK, "track_credits": float(est or 3.0),
        "satisfies": [program["id"]], "tracks": tracks, "note": node.get("note"),
    }


def _cards_credits(cards: list[dict]) -> float:
    return sum(float(c.get("credits") or 0) for c in cards)


def _collect(nodes: list[dict], program: dict, cc: dict[str, float], completed_set: set[str]) -> list[dict]:
    """Flatten a requirement subtree into ordered plan cards (course cards + slot cards).

    * all_of / group   -> each course is a forced (locked) card; recurse into children
    * one_of (1 course) -> forced card
    * one_of (N courses)-> default card (first option) with the rest as swappable alternatives
    * choose            -> one constrained slot per pick (N picks, or ceil(credits/3) slots)
    * credits placeholder -> open slots (split into 3-credit chunks)
    * track_select      -> a single track-chooser slot
    """
    cards: list[dict] = []
    for n in nodes:
        kind = n.get("kind", "all_of")
        name = n.get("name") or n.get("label") or ""

        if kind in ("all_of", "group"):
            for code in n.get("courses", []):
                if engine.normalize_code(code) not in completed_set:
                    cards.append(_course_card(program, code, cc))
            cards += _collect(n.get("children", []), program, cc, completed_set)

        elif kind == "one_of":
            courses = [engine.normalize_code(c) for c in n.get("courses", [])]
            children = n.get("children", [])
            if courses and any(c in completed_set for c in courses):
                continue  # already satisfied by a completed course
            if len(courses) == 1 and not children:
                cards.append(_course_card(program, courses[0], cc))
            elif len(courses) >= 2:
                card = _course_card(program, courses[0], cc)
                card["alternatives"] = courses[1:]
                cards.append(card)
            elif children:  # one_of over nested nodes -> a constrained slot
                opts = _flatten_option_codes(children) or [
                    engine.normalize_code(c) for ch in children for c in ch.get("courses", [])
                ]
                opts = [c for c in opts if c not in completed_set]
                if opts:
                    cards.append(_choice_slot(program, name, opts, n))

        elif kind == "choose":
            opts = [c for c in _flatten_option_codes(n.get("options", n.get("courses", [])))
                    if c not in completed_set]
            if not opts:
                continue
            if n.get("choose_credits"):
                k = max(1, math.ceil(float(n["choose_credits"]) / OPEN_SLOT_CHUNK))
            else:
                k = int(n.get("choose") or 1)
            for i in range(k):
                cards.append(_choice_slot(program, name, opts, n, index=i, total=k))

        elif kind == "credits":
            if n.get("placeholder"):
                cards += _open_slots(program, name, n.get("credits") or OPEN_SLOT_CHUNK, n)
            # non-placeholder credit buckets are auto-satisfied by matching courses already
            # placed for other requirements — nothing to add here.

        elif kind == "track_select":
            cards.append(_track_slot(program, n, cc, completed_set))

    return cards


def program_cards(program: dict, cc: dict[str, float], completed_set: set[str]) -> tuple[list[dict], list[dict]]:
    """(forced_course_cards, slot_cards) for a whole program, from its requirement tree."""
    cards = _collect(program.get("requirements", []), program, cc, completed_set)
    courses = [c for c in cards if c.get("code")]
    slots = [c for c in cards if c.get("slot")]
    return courses, slots


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------


def scaffold(
    program_ids: list[str],
    completed: list[dict],
    constraints: dict[str, Any],
    store,
    catalog_credits: dict[str, float] | None = None,
) -> dict[str, Any]:
    cc = catalog_credits or {}
    start = constraints.get("start_term") or "Fall 2026"
    max_credits = float(constraints.get("max_credits") or DEFAULT_MAX_CREDITS)
    use_summers = bool(constraints.get("use_summers"))
    # Expand completed credit through equivalences so a held MA 16500 satisfies a plan that
    # lists MA 16100, and MA 26200 covers both MA 26500 and MA 26600 — i.e. transfer credit
    # actually removes the courses it fulfils instead of scheduling them anyway.
    completed_set = equivalence.expand(
        engine.normalize_code(c["code"]) for c in completed if c.get("code")
    )

    programs = [p for p in (store.get(pid) for pid in program_ids) if p]
    if not programs:
        return {"semesters": [], "source": "none", "notes": ["No valid programs selected."], "programs": []}

    primary = next((p for p in programs if p.get("recommended_sequence")), None)
    notes: list[str] = []

    if primary:
        semesters = _from_official(primary, completed_set, start, use_summers, cc)
        _reconcile_with_tree(semesters, primary, completed_set, cc, max_credits, start, use_summers)
        source = "official"
        notes.append(f"Backbone from the official sample plan for {primary['name']}.")
    else:
        primary = programs[0]
        semesters = _from_solver(primary, completed_set, start, max_credits, use_summers, cc)
        source = "solver"
        notes.append(f"No official sample plan — generated from the requirement tree for {primary['name']}.")

    placed = {engine.normalize_code(c["code"]) for s in semesters for c in s["courses"] if c.get("code")}

    # Overlay the other selected programs: their forced courses + selective / concentration
    # slots that aren't already present. Open gen-ed buckets come from the primary only.
    for prog in programs:
        if prog is primary:
            continue
        courses, slots = program_cards(prog, cc, completed_set)
        extra_courses = [c for c in courses if engine.normalize_code(c["code"]) not in placed and engine.normalize_code(c["code"]) not in completed_set]
        extra_slots = [s for s in slots if s.get("slotKind") != "open"]
        _bin_pack(semesters, extra_courses + extra_slots, max_credits, start, use_summers)
        placed |= {engine.normalize_code(c["code"]) for c in extra_courses}
        if extra_courses or extra_slots:
            notes.append(f"Added {len(extra_courses)} course(s) + {len(extra_slots)} selective slot(s) from {prog['name']}.")

    removed = _dedupe_equivalents(semesters)
    if removed:
        notes.append(f"Removed {len(removed)} redundant course(s) — an equivalent is already in the plan ({', '.join(sorted(removed))}).")

    trimmed = _trim_open_slots_for_completed(semesters, programs, completed, completed_set, cc)
    if trimmed > 0:
        notes.append(f"Trimmed ~{round(trimmed)} cr of open elective slots already covered by your transfer/AP credit.")

    _tag_shared(semesters, programs)
    return {"semesters": semesters, "source": source, "notes": notes, "programs": program_ids}


def _trim_open_slots_for_completed(semesters, programs, completed, completed_set, cc) -> float:
    """Make transfer/AP credit *shrink the plan*, not just drop named courses.

    Completed credit that maps to a specific required course already removed that course from
    the plan. Whatever's left over (generic 1xxx / elective / IB / AP that doesn't map to a
    named requirement) should instead cancel out open elective slots, so plan credits + prior
    credits land near the degree total — feedback: "transfer doesn't seem to do anything" and
    "1xxx credit should count toward the 120." Removes open slots from the latest terms first.
    """
    countable: set[str] = set()
    for p in programs:
        countable |= countable_codes(p)
    leftover = 0.0
    for c in completed:
        code = engine.normalize_code(c.get("code", "")) if c.get("code") else ""
        if not code:
            continue
        credit = float(c.get("credits") or cc.get(code) or 3.0)
        # Credit that satisfies a named requirement already pulled its course; don't double-count.
        if equivalence.expand([code]) & countable:
            continue
        leftover += credit
    if leftover <= 0:
        return 0.0

    trimmed = 0.0
    for sem in reversed(semesters):
        if leftover - trimmed <= 0:
            break
        keep: list[dict] = []
        # walk this term's cards back-to-front so we peel electives off the end
        for card in reversed(sem["courses"]):
            cr = float(card.get("credits") or 0)
            if card.get("slotKind") == "open" and (leftover - trimmed) >= cr - 0.01:
                trimmed += cr
                continue
            keep.append(card)
        sem["courses"] = list(reversed(keep))
    return trimmed


def _dedupe_equivalents(semesters: list[dict]) -> list[str]:
    """Drop a scheduled course when an interchangeable one is already in the plan.

    A math+engineering double major otherwise gets *both* MA 26500 and MA 35100 (same
    requirement, mutually exclusive for credit) — feedback called this out as physically
    impossible. The first-scheduled member is kept; ``_tag_shared`` re-attributes the survivor
    to every program it now counts toward. Also drops a course a combined course covers
    (MA 26500 when MA 26200 is present)."""
    all_codes = {engine.normalize_code(c["code"]) for s in semesters for c in s["courses"] if c.get("code")}
    covered_redundant: set[str] = set()
    for code in all_codes:
        for cov in equivalence._COVERS_NORM.get(code, []):
            if cov in all_codes:
                covered_redundant.add(cov)

    seen_groups: set[str] = set()
    removed: list[str] = []
    for sem in semesters:
        kept: list[dict] = []
        for c in sem["courses"]:
            code = c.get("code")
            if not code:
                kept.append(c)
                continue
            code = engine.normalize_code(code)
            gkey = equivalence.group_key(code)
            duplicate_group = gkey in seen_groups and len(equivalence.equivalents(code)) > 1
            if code in covered_redundant or duplicate_group:
                removed.append(code)
                continue
            seen_groups.add(gkey)
            kept.append(c)
        sem["courses"] = kept
    return removed


def _from_official(program, completed_set, start, use_summers, cc) -> list[dict]:
    seq = program["recommended_sequence"]
    # Official sample plans are laid out in Fall/Spring terms only, so map them onto a pure
    # Fall→Spring calendar even when summers are enabled — otherwise a regular ~15-credit term
    # lands on a summer slot, blowing past the 9-credit summer ceiling. Summers are still used
    # by the overlay/bin-packer to absorb extra courses.
    cal = term_sequence(start, len(seq), use_summers=False)
    req = required_codes(program)
    choice_index = _tree_choice_index(program)
    semesters: list[dict] = []
    for cal_term, term in zip(cal, seq):
        courses: list[dict] = []
        for it in term["items"]:
            if it.get("slot"):
                label = it.get("label", "Selective")
                opts = it.get("options") or _match_options(label, choice_index)
                courses.append({
                    "slot": True, "slotKind": "choose" if opts else "open",
                    "label": label, "credits": float(it.get("credits") or 3),
                    "satisfies": [program["id"]], "options": opts,
                })
                continue
            code = engine.normalize_code(it["code"])
            if code in completed_set:
                continue
            card = {
                "code": code, "title": it.get("title", ""),
                "credits": float(it.get("credits") or credit_of(program, code, cc)),
                "satisfies": [program["id"]], "locked": code in req,
            }
            if it.get("alternatives"):
                card["alternatives"] = [engine.normalize_code(a["code"]) for a in it["alternatives"] if a.get("code")]
            courses.append(card)
        semesters.append({"term": cal_term, "courses": courses})
    return semesters


def _from_solver(program, completed_set, start, max_credits, use_summers, cc) -> list[dict]:
    """Read the requirement tree directly, then greedily pack into terms.

    Lower course numbers go first (100s before 200s …), a decent proxy for prerequisite
    depth; the client's auto-fix pass then nudges anything still out of prereq order."""
    courses, slots = program_cards(program, cc, completed_set)
    courses.sort(key=lambda c: (engine.code_number(c["code"]) or 0))
    semesters = [{"term": t, "courses": []} for t in term_sequence(start, 8, use_summers)]
    _bin_pack(semesters, courses, max_credits, start, use_summers)
    _bin_pack(semesters, slots, max_credits, start, use_summers)
    return semesters


def _reconcile_with_tree(semesters, program, completed_set, cc, max_credits, start, use_summers) -> None:
    """Make sure an official sample plan isn't silently missing requirement-tree content.

    Conservative on purpose: a sample plan already lays out selectives (as named slots), so
    re-adding the tree's ``choose`` slots would double up. We only add what a sample plan
    reliably omits — a ``track_select`` concentration (usually buried as a generic
    'selective') and any flat-out required course the plan skipped entirely."""
    courses, slots = program_cards(program, cc, completed_set)
    placed = {engine.normalize_code(c["code"]) for s in semesters for c in s["courses"] if c.get("code")}
    has_track = any(c.get("slotKind") == "track" for s in semesters for c in s["courses"])

    add: list[dict] = []
    for c in courses:
        if engine.normalize_code(c["code"]) not in placed:
            add.append(c)
    if not has_track:
        add.extend(s for s in slots if s.get("slotKind") == "track")
    if add:
        _bin_pack(semesters, add, max_credits, start, use_summers)


def _tree_choice_index(program: dict) -> list[tuple[str, list[str]]]:
    """(name, approved-options) for every choose / multi-course one_of, used to attach
    options to sample-plan slots by matching their labels."""
    out: list[tuple[str, list[str]]] = []
    for n in _walk_all(program.get("requirements", [])):
        kind = n.get("kind")
        name = (n.get("name") or n.get("label") or "").strip()
        if not name:
            continue
        if kind == "choose":
            opts = _flatten_option_codes(n.get("options", n.get("courses", [])))
            if opts:
                out.append((name, opts))
        elif kind == "one_of" and len(n.get("courses", [])) >= 2:
            out.append((name, [engine.normalize_code(c) for c in n["courses"]]))
    return out


# Words too generic to imply two requirement labels are the same selective.
_GENERIC_WORDS = {
    "selective", "selectives", "elective", "electives", "core", "course", "courses",
    "credit", "credits", "requirement", "requirements", "choose", "choice", "option",
    "options", "general", "education", "univ", "university", "curriculum", "area",
    "group", "additional", "approved", "list", "free", "track", "concentration",
    "emphasis", "major", "minor", "level", "and", "the", "for", "from", "pick",
}


def _sig_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) > 2 and w not in _GENERIC_WORDS}


def _match_options(label: str, choice_index: list[tuple[str, list[str]]]) -> list[str]:
    """Conservatively attach a requirement-tree choice node's approved options to a
    sample-plan slot. Only matches on *significant* shared words (ignoring filler like
    'selective'/'core'), so a 'Calculus Selective' isn't constrained to accounting courses.
    No confident match -> empty (the slot stays an open picker)."""
    lt = _sig_tokens(label)
    if not lt:
        return []
    for name, opts in choice_index:
        nt = _sig_tokens(name)
        if nt and (lt == nt or lt <= nt or nt <= lt):
            return opts
    return []


def _term_credits(sem: dict) -> float:
    return sum(float(c.get("credits") or 0) for c in sem["courses"])


def _term_cap(term: str, max_credits: float) -> float:
    """Per-term credit ceiling — summer terms carry roughly half a normal load."""
    season, _ = parse_term(term)
    return min(max_credits, SUMMER_MAX_CREDITS) if season == "Summer" else max_credits


def _bin_pack(semesters: list[dict], cards: list[dict], max_credits: float, start: str, use_summers: bool) -> None:
    """Greedily drop cards into the earliest term with room, extending the plan as needed."""
    for card in cards:
        placed = False
        for sem in semesters:
            if _term_credits(sem) + float(card.get("credits") or 0) <= _term_cap(sem["term"], max_credits):
                sem["courses"].append(card)
                placed = True
                break
        if not placed:
            last = semesters[-1]["term"] if semesters else start
            nxt = term_sequence(_next_term(last, use_summers), 1, use_summers)[0]
            semesters.append({"term": nxt, "courses": [card]})


def _next_term(term: str, use_summers: bool) -> str:
    season, year = parse_term(term)
    if season == "Fall":
        return f"Spring {year + 1}"
    if season == "Spring":
        return f"Summer {year}" if use_summers else f"Fall {year}"
    return f"Fall {year}"


def _tag_shared(semesters: list[dict], programs: list[dict]) -> None:
    """Mark a course's `satisfies` with every selected program that could count it."""
    countable_by_prog = {p["id"]: countable_codes(p) for p in programs}
    for sem in semesters:
        for c in sem["courses"]:
            if not c.get("code"):
                continue
            code_exp = equivalence.expand([engine.normalize_code(c["code"])])
            owners = [pid for pid, codes in countable_by_prog.items() if code_exp & codes]
            if owners:
                merged = list(dict.fromkeys((c.get("satisfies") or []) + owners))
                c["satisfies"] = merged
