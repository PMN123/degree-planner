#!/usr/bin/env python3
"""Program-requirement store.

Three layers, merged by slug (later layers win):

1. ``programs_index.json``      — every Purdue program (scraped index). Powers the
                                  searchable major / minor pickers even before a
                                  program's requirement tree has been scraped.
2. ``programs/generated/*.json`` — best-effort requirement trees from the scraper
                                  (``"verified": false``).
3. ``programs/verified/*.json``  — human-verified trees that override generated ones.

Shared ``{"$include": "name"}`` fragments (calculus, university core, …) are resolved
from ``programs/_shared.json`` for hand-authored files.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import normalize

HERE = Path(__file__).resolve().parent
PROGRAMS_DIR = HERE / "programs"
SHARED_PATH = PROGRAMS_DIR / "_shared.json"
VERIFIED_DIR = PROGRAMS_DIR / "verified"
GENERATED_DIR = PROGRAMS_DIR / "generated"
INDEX_PATH = HERE.parent / "programs_index.json"

TYPE_ORDER = {"major": 0, "minor": 1, "certificate": 2, "other": 3, "graduate": 4}


def _load_shared() -> dict[str, Any]:
    if SHARED_PATH.exists():
        return json.loads(SHARED_PATH.read_text(encoding="utf-8")).get("blocks", {})
    return {}


def _resolve(node: Any, blocks: dict[str, Any]) -> Any:
    """Recursively replace any {"$include": name} dicts with the shared block."""
    if isinstance(node, dict):
        if "$include" in node:
            name = node["$include"]
            block = blocks.get(name)
            if block is None:
                return {"id": name, "name": f"(missing include: {name})", "kind": "all_of", "courses": []}
            resolved = copy.deepcopy(block)
            for key, value in node.items():
                if key != "$include":
                    resolved[key] = value
            return _resolve(resolved, blocks)
        return {k: _resolve(v, blocks) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(item, blocks) for item in node]
    return node


def _meta_from_program(program: dict[str, Any], *, verified: bool) -> dict[str, Any]:
    return {
        "id": program["id"],
        "name": program.get("name"),
        "type": program.get("type", "major"),
        "degree": program.get("degree"),
        "college": program.get("college"),
        "total_credits": program.get("total_credits"),
        "catalog_year": program.get("catalog_year"),
        "source_url": program.get("source_url"),
        "poid": program.get("poid"),
        "verified": verified,
        "has_requirements": bool(program.get("requirements")),
    }


class ProgramStore:
    def __init__(self) -> None:
        self._full: dict[str, dict[str, Any]] = {}     # slug -> full requirement program
        self._meta: dict[str, dict[str, Any]] = {}     # slug -> lightweight list entry
        self.load()

    def load(self) -> None:
        blocks = _load_shared()
        self._full = {}
        self._meta = {}

        # 1. searchable index (lightweight)
        if INDEX_PATH.exists():
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            for p in data.get("programs", []):
                slug = p.get("slug") or p.get("id")
                if not slug:
                    continue
                self._meta[slug] = {
                    "id": slug, "name": p.get("name"), "type": p.get("type", "major"),
                    "degree": p.get("degree"), "college": p.get("college"),
                    "total_credits": p.get("total_credits"), "source_url": p.get("source_url"),
                    "poid": p.get("poid"), "verified": False, "has_requirements": False,
                }

        # 2. generated trees, then 3. verified trees (verified wins)
        for directory, verified in ((GENERATED_DIR, False), (VERIFIED_DIR, True)):
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.json")):
                if path.name.startswith("_"):
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path.name}: invalid JSON ({exc})") from exc
                program = _resolve(raw, blocks)
                slug = program.get("id") or path.stem
                program["id"] = slug
                program["verified"] = verified
                # Generated trees are best-effort parses — repair mis-parsed "choose N from a
                # menu" requirements the scraper flattened into all_of (else a 15-credit minor
                # schedules ~96 "required" courses → an impossible plan). Verified files are
                # hand-correct and left untouched.
                if not verified:
                    program = normalize.normalize_program(program)
                self._full[slug] = program
                self._meta[slug] = _meta_from_program(program, verified=verified)

    # -- queries ------------------------------------------------------------
    def list(self, *, type: str | None = None, q: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        items = list(self._meta.values())
        if type:
            wanted = {t.strip() for t in type.split(",")}
            items = [p for p in items if p["type"] in wanted]
        if q:
            ql = q.strip().lower()
            items = [p for p in items if ql in (p["name"] or "").lower() or ql in p["id"].lower()]
        items.sort(key=lambda p: (TYPE_ORDER.get(p["type"], 9), (p["name"] or "").lower()))
        return items[:limit] if limit else items

    def get(self, program_id: str) -> dict[str, Any] | None:
        if program_id in self._full:
            return self._full[program_id]
        meta = self._meta.get(program_id)
        if not meta:
            return None
        # Known program, but its requirement tree hasn't been scraped yet.
        return {
            **meta, "requirements": [], "recommended_sequence": [],
            "needs_scrape": True,
            "notes": ["Requirements for this program have not been scraped yet."],
        }

    def slugs(self) -> list[str]:
        return list(self._meta.keys())
