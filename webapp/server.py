#!/usr/bin/env python3
"""Zero-dependency web server for the Boiler Degree Planner.

Built entirely on the Python standard library (``http.server``) so the whole app
runs with ``python webapp/server.py`` — no Flask, no npm, no build step. It serves
the static single-page UI and a small JSON API backed by the generic requirements
engine, the scraped course catalog, and the program-requirement store.

Endpoints
---------
GET  /api/meta                      catalog size, term, program count
GET  /api/programs                  list available programs (majors / minors)
GET  /api/programs/{id}             full requirement tree for one program
GET  /api/courses/search?q=...      search the course catalog
GET  /api/courses/{code}            one course detail (on-demand scrape fallback)
POST /api/courses/ensure            {"code": "..."} fetch a course if missing
POST /api/audit                     audit a posted plan against selected programs
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import engine
from catalog import Catalog, audit_prerequisites
from programs_store import ProgramStore

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"

CATALOG = Catalog()
PROGRAMS = ProgramStore()

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
}


# ---------------------------------------------------------------------------
# Plan auditing
# ---------------------------------------------------------------------------


def build_credit_lookup(plan: dict) -> dict[str, float]:
    extra: dict[str, float] = {}
    for row in plan.get("completed", []):
        if row.get("credits"):
            extra[engine.normalize_code(row["code"])] = float(row["credits"])
    for sem in plan.get("semesters", []):
        for row in sem.get("courses", []):
            if row.get("credits"):
                extra[engine.normalize_code(row["code"])] = float(row["credits"])
    return CATALOG.credit_lookup(extra)


def available_codes(plan: dict) -> set[str]:
    codes: set[str] = set()
    for row in plan.get("completed", []):
        code = engine.normalize_code(row["code"])
        if not code.startswith("PLACEHOLDER"):
            codes.add(code)
    for sem in plan.get("semesters", []):
        for row in sem.get("courses", []):
            code = engine.normalize_code(row["code"])
            if not code.startswith("PLACEHOLDER"):
                codes.add(code)
    return codes


def semester_loads(plan: dict) -> list[dict]:
    loads = []
    for sem in plan.get("semesters", []):
        courses = sem.get("courses", [])
        credits = sum(float(c.get("credits", 0) or 0) for c in courses)
        flag = None
        if credits > 18:
            flag = "heavy"
        elif 0 < credits < 12:
            flag = "light"
        loads.append({"term": sem.get("term", "Term"), "credits": round(credits, 1), "count": len(courses), "flag": flag})
    return loads


def run_audit(plan: dict) -> dict:
    available = available_codes(plan)
    credits = build_credit_lookup(plan)

    program_results = []
    used_by: dict[str, list[str]] = {}
    for pid in plan.get("programs", []):
        program = PROGRAMS.get(pid)
        if not program:
            continue
        result = engine.evaluate_program(program, available, credits)
        program_results.append(result)
        for code in result["used_courses"]:
            used_by.setdefault(code, []).append(result["name"] + (f" {result['degree']}" if result.get("degree") else ""))

    overlaps = {code: progs for code, progs in used_by.items() if len(progs) > 1}

    prereq = audit_prerequisites(CATALOG, plan.get("completed", []), plan.get("semesters", []))

    completed_credits = sum(float(c.get("credits", 0) or 0) for c in plan.get("completed", []))
    planned_credits = sum(
        float(c.get("credits", 0) or 0) for sem in plan.get("semesters", []) for c in sem.get("courses", [])
    )
    targets = [p.get("total_credits") for p in program_results if p.get("total_credits")]
    degree_target = max(targets) if targets else 120

    return {
        "credits": {
            "completed": round(completed_credits, 1),
            "planned": round(planned_credits, 1),
            "total": round(completed_credits + planned_credits, 1),
            "degree_target": degree_target,
            "target_met": (completed_credits + planned_credits) >= degree_target,
        },
        "programs": program_results,
        "loads": semester_loads(plan),
        "overlaps": overlaps,
        "prerequisites": {
            "ok": prereq["ok"],
            "checks": prereq["checks"],
            "warnings": prereq["warnings"],
        },
        "feasible": all(p["satisfied"] for p in program_results)
        and prereq["ok"]
        and (completed_credits + planned_credits) >= degree_target,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "BoilerDegreePlanner/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- helpers ----------------------------------------------------------
    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _serve_static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC_DIR)) or not target.is_file():
            self.send_error(404, "Not found")
            return
        data = target.read_bytes()
        ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            return self._serve_static(path)

        if path == "/api/meta":
            return self._json(
                {
                    "catalog_courses": CATALOG.count(),
                    "catalog_term": CATALOG.term,
                    "subjects": CATALOG.subjects(),
                    "programs": len(PROGRAMS.list()),
                }
            )
        if path == "/api/programs":
            return self._json({"programs": PROGRAMS.list()})
        m = re.match(r"^/api/programs/([^/]+)$", path)
        if m:
            program = PROGRAMS.get(unquote(m.group(1)))
            if not program:
                return self._json({"error": "program not found"}, 404)
            return self._json(program)
        if path == "/api/courses/search":
            q = parse_qs(parsed.query).get("q", [""])[0]
            return self._json({"results": CATALOG.search(q)})
        m = re.match(r"^/api/courses/(.+)$", path)
        if m:
            code = unquote(m.group(1))
            summary = CATALOG.summary(code)
            if summary:
                return self._json({"course": summary, "cached": True})
            result = CATALOG.ensure_course(code)
            status = 200 if result.get("ok") else 404
            return self._json(result, status)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/audit":
            plan = self._read_json()
            try:
                return self._json(run_audit(plan))
            except Exception as exc:  # never 500 silently — report it
                return self._json({"error": f"audit failed: {exc}"}, 500)
        if path == "/api/courses/ensure":
            body = self._read_json()
            code = body.get("code", "")
            if not code:
                return self._json({"error": "code required"}, 400)
            return self._json(CATALOG.ensure_course(code))
        return self._json({"error": "not found"}, 404)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print("\n  Boiler Degree Planner")
    print(f"  Catalog: {CATALOG.count()} courses (term {CATALOG.term}) · {len(PROGRAMS.list())} programs")
    print(f"  Serving at {url}")
    print("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
