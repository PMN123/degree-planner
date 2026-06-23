#!/usr/bin/env python3
"""Course equivalence + coverage, shared by the engine, scheduler, and prereq audit.

Purdue has several courses that are *interchangeable* for satisfying a requirement or a
prerequisite — taking more than one is redundant (often literally not allowed for credit):

* Calculus I    — MA 16100 / MA 16500 / MA 16700
* Calculus II   — MA 16200 / MA 16600 / MA 16800
* Multivariate  — MA 26100 / MA 27101
* Linear Algebra— MA 26500 / MA 35100   (engineering takes 265, math takes 351)
* Diff. Eqs.    — MA 26600 / MA 36600   (engineering takes 266, math takes 366)

and a few *combined* courses that cover two others at once:

* MA 26200 ("Linear Algebra & Differential Equations") covers BOTH MA 26500 and MA 26600.

Without this, the planner would (a) refuse to recognise MA 16500 when a sample plan literally
lists MA 16100, (b) schedule both MA 26500 *and* MA 35100 for a math+engineering double major
even though they're the same requirement, and (c) ignore transfer credit that came in under the
"other" code in a pair. All three were reported in feedback.

The data lives here (not in ``_shared.json``) because ``engine`` / ``scheduler`` / ``catalog``
all need it and none of them import the program store.
"""

from __future__ import annotations

import re

# Each inner list is a set of mutually-equivalent course codes: any one satisfies a
# requirement / prerequisite that names any other, and scheduling more than one is redundant.
EQUIVALENT_GROUPS: list[list[str]] = [
    ["MA 16100", "MA 16500", "MA 16700"],   # Calculus I
    ["MA 16200", "MA 16600", "MA 16800"],   # Calculus II
    ["MA 26100", "MA 27101"],               # Multivariate / Calculus III
    ["MA 26500", "MA 35100"],               # Linear Algebra
    ["MA 26600", "MA 36600"],               # Ordinary Differential Equations
]

# A single course that, on its own, fulfils several others (a combined course).
# Expanded transitively through EQUIVALENT_GROUPS, so MA 26200 also covers MA 35100 / MA 36600.
COVERS: dict[str, list[str]] = {
    "MA 26200": ["MA 26500", "MA 26600"],   # Lin. Alg. & Diff. Eqs. = 265 + 266
}


def _normalize(code: str) -> str:
    value = " ".join(str(code).strip().upper().replace("-", " ").split())
    m = re.match(r"^([A-Z]+)\s*([0-9][0-9A-Z]{2,5})$", value)
    if m:
        subject, number = m.groups()
        if number.isdigit() and len(number) < 5:
            number = number.zfill(5)
        return f"{subject} {number}"
    return value


# Build, once, a map code -> set of every code it is interchangeable with (itself included).
_GROUP_OF: dict[str, frozenset[str]] = {}
for _grp in EQUIVALENT_GROUPS:
    _norm = frozenset(_normalize(c) for c in _grp)
    for _c in _norm:
        _GROUP_OF[_c] = _norm

_COVERS_NORM: dict[str, list[str]] = {
    _normalize(k): [_normalize(v) for v in vs] for k, vs in COVERS.items()
}


def equivalents(code: str) -> frozenset[str]:
    """Every code interchangeable with ``code`` (always includes ``code`` itself)."""
    code = _normalize(code)
    return _GROUP_OF.get(code, frozenset({code}))


def group_key(code: str) -> str:
    """A stable identifier for ``code``'s equivalence group (the lexically-smallest member).

    Two codes share a ``group_key`` iff they're interchangeable — handy for de-duplicating a
    plan that scheduled both members of a pair.
    """
    return min(equivalents(code))


def expand(codes) -> set[str]:
    """Expand a set of held/planned codes to everything they also satisfy.

    For each code: add its equivalents, then for combined courses add what they cover (and the
    equivalents of *those*). This is what makes "I have MA 16500" satisfy a plan that lists
    MA 16100, and "I have MA 26200" satisfy both a linear-algebra and a diff-eq requirement.
    """
    out: set[str] = set()
    for raw in codes:
        code = _normalize(raw)
        out |= set(equivalents(code))
        for covered in _COVERS_NORM.get(code, []):
            out |= set(equivalents(covered))
    return out
