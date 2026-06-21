#!/usr/bin/env python3
"""Loads program-requirement files and resolves shared ``$include`` fragments."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

PROGRAMS_DIR = Path(__file__).resolve().parent / "programs"
SHARED_PATH = PROGRAMS_DIR / "_shared.json"


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
            # allow per-use overrides (e.g. a custom name)
            for key, value in node.items():
                if key != "$include":
                    resolved[key] = value
            return _resolve(resolved, blocks)
        return {k: _resolve(v, blocks) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(item, blocks) for item in node]
    return node


class ProgramStore:
    def __init__(self, directory: Path = PROGRAMS_DIR):
        self.directory = directory
        self._programs: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        blocks = _load_shared()
        self._programs = {}
        for path in sorted(self.directory.glob("*.json")):
            if path.name.startswith("_"):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}: invalid JSON ({exc})") from exc
            program = _resolve(raw, blocks)
            pid = program.get("id") or path.stem
            program["id"] = pid
            self._programs[pid] = program

    def list(self) -> list[dict[str, Any]]:
        out = []
        for program in self._programs.values():
            out.append(
                {
                    "id": program["id"],
                    "name": program.get("name"),
                    "degree": program.get("degree"),
                    "type": program.get("type", "major"),
                    "college": program.get("college"),
                    "total_credits": program.get("total_credits"),
                    "catalog_year": program.get("catalog_year"),
                }
            )
        out.sort(key=lambda p: (p["type"] != "major", p["college"] or "", p["name"] or ""))
        return out

    def get(self, program_id: str) -> dict[str, Any] | None:
        return self._programs.get(program_id)
