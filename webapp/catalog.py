#!/usr/bin/env python3
"""Course-catalog access layer for the web app.

Wraps the scraped ``course_catalog.json`` with search / detail / credit lookups,
reuses the (excellent) structured prerequisite parser from ``audit_plan.py``, and
exposes optional *on-demand* scraping so the planner can pull a course the user
asks about even if it was not in the original scrape list.

Everything degrades gracefully: if ``requests`` / ``beautifulsoup4`` are not
installed, search and auditing still work against whatever is already cached, and
on-demand scraping simply reports that it is unavailable.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

# The web app lives in degree-planner/webapp; data + scripts live one level up.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import audit_plan as legacy  # noqa: E402  (reuse the proven prereq/restriction engine)

CATALOG_PATH = ROOT / "course_catalog.json"
DEFAULT_TERM = "202710"

_lock = threading.Lock()


class Catalog:
    def __init__(self, path: Path = CATALOG_PATH):
        self.path = path
        self._data: dict[str, Any] = {"courses": {}}
        self.load()

    # -- loading ------------------------------------------------------------
    def load(self) -> None:
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        self._data.setdefault("courses", {})

    @property
    def courses(self) -> dict[str, Any]:
        return self._data["courses"]

    @property
    def term(self) -> str:
        return str(self._data.get("term", DEFAULT_TERM))

    def count(self) -> int:
        return len(self.courses)

    def subjects(self) -> list[str]:
        subs = {legacy.normalize_code(c).split()[0] for c in self.courses}
        return sorted(subs)

    # -- lookups ------------------------------------------------------------
    def get(self, code: str) -> dict[str, Any] | None:
        return self.courses.get(legacy.normalize_code(code))

    def summary(self, code: str) -> dict[str, Any] | None:
        rec = self.get(code)
        if not rec:
            return None
        return self._summarize(rec)

    @staticmethod
    def _summarize(rec: dict[str, Any]) -> dict[str, Any]:
        credits = rec.get("credits") or {}
        return {
            "code": rec.get("code"),
            "title": rec.get("title"),
            "subject": rec.get("subject"),
            "number": rec.get("number"),
            "credits": credits.get("max") if isinstance(credits, dict) else None,
            "department": rec.get("department"),
            "description": rec.get("description"),
            "prerequisites_text": rec.get("prerequisites_text"),
            "prerequisite_courses": rec.get("prerequisite_courses", []),
            "restrictions_text": rec.get("restrictions_text"),
            "schedule_types": rec.get("schedule_types", []),
            "campuses": rec.get("campuses", []),
            "url": rec.get("url"),
            "status": rec.get("status"),
        }

    def search(self, query: str, limit: int = 40) -> list[dict[str, Any]]:
        query = (query or "").strip().lower()
        results: list[tuple[int, dict[str, Any]]] = []
        for code, rec in self.courses.items():
            title = (rec.get("title") or "").lower()
            ncode = code.lower()
            score = None
            if not query:
                score = 0
            elif ncode.replace(" ", "").startswith(query.replace(" ", "")):
                score = 100
            elif query in ncode:
                score = 80
            elif title.startswith(query):
                score = 60
            elif query in title:
                score = 40
            elif query in (rec.get("description") or "").lower():
                score = 10
            if score is not None:
                results.append((score, self._summarize(rec)))
        results.sort(key=lambda r: (-r[0], r[1]["code"]))
        return [r[1] for r in results[:limit]]

    def credit_lookup(self, extra: dict[str, float] | None = None) -> dict[str, float]:
        lookup: dict[str, float] = {}
        for code, rec in self.courses.items():
            credits = rec.get("credits") or {}
            if isinstance(credits, dict) and credits.get("max") is not None:
                lookup[legacy.normalize_code(code)] = float(credits["max"])
        if extra:
            for k, v in extra.items():
                lookup[legacy.normalize_code(k)] = float(v)
        return lookup

    # -- on-demand scraping -------------------------------------------------
    def ensure_course(self, code: str, term: str | None = None) -> dict[str, Any]:
        """Fetch one course from the live catalog if we don't already have it."""
        code = legacy.normalize_code(code)
        if code in self.courses:
            return {"ok": True, "cached": True, "course": self._summarize(self.courses[code])}
        try:
            import scrape_courses as scraper  # noqa: PLC0415
            import requests  # noqa: PLC0415
        except Exception as exc:  # requests/bs4 missing
            return {"ok": False, "error": f"scraping unavailable: {exc}", "course": None}

        parts = code.split()
        if len(parts) != 2:
            return {"ok": False, "error": f"cannot parse code: {code}", "course": None}
        subject, number = parts
        term = term or self.term
        request = scraper.CourseRequest(subject=subject, number=number)
        session = requests.Session()
        session.headers.update({"User-Agent": "boiler-degree-planner/1.0"})
        cache_dir = ROOT / "cache"
        try:
            html, status, url, from_cache, fetch_error = scraper.fetch_html(
                session, request, term, cache_dir, refresh=False, timeout=25
            )
            record = scraper.parse_course(html or "", request, term, url, status, from_cache, fetch_error)
        except Exception as exc:
            return {"ok": False, "error": f"fetch failed: {exc}", "course": None}

        with _lock:
            self.courses[code] = record
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        ok = record.get("status") == "ok"
        return {
            "ok": ok,
            "cached": False,
            "error": None if ok else f"catalog status: {record.get('status')}",
            "course": self._summarize(record),
        }


# ---------------------------------------------------------------------------
# Prerequisite auditing (reuses audit_plan's parser via CourseInstance objects)
# ---------------------------------------------------------------------------


def _course_instances(rows: list[dict[str, Any]], term: str, source: str) -> list[legacy.CourseInstance]:
    out = []
    for row in rows:
        out.append(
            legacy.CourseInstance(
                code=legacy.normalize_code(row["code"]),
                title=row.get("title", ""),
                credits=float(row.get("credits", 0) or 0),
                term=term,
                source=source,
                raw=row,
            )
        )
    return out


def audit_prerequisites(
    catalog: Catalog,
    completed: list[dict[str, Any]],
    semesters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the structured AND/OR prerequisite audit on a posted plan."""
    completed_inst = _course_instances(completed, "Completed", "Completed")
    planned_inst: list[legacy.CourseInstance] = []
    for sem in semesters:
        planned_inst.extend(_course_instances(sem.get("courses", []), sem.get("term", "Term"), "Planned"))
    return legacy.audit_prerequisites(catalog._data, completed_inst, planned_inst)
