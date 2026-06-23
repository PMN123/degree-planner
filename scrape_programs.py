#!/usr/bin/env python3
"""Scrape Purdue catalog *programs* (majors / minors / certificates) into:

- ``programs_index.json``                       every program: poid, name, type, degree, slug, url
- ``webapp/programs/generated/<slug>.json``     best-effort requirement tree + official 4-year
                                                sample plan, flagged ``"verified": false``

``catalog.purdue.edu`` sits behind AWS WAF, which serves a JavaScript *challenge*
(HTTP 202, ``x-amzn-waf-action: challenge``) on ``content.php`` / ``preview_program.php``.
Plain ``requests`` can't compute the token, so ``scrape/waf_fetch.mjs`` (headless Chrome)
mints an ``aws-waf-token`` cookie once; we reuse it here for the fast bulk fetch and
re-mint automatically if it expires. Cache-aware, polite, and never fatal — mirroring
the design of :mod:`scrape_courses`.

Usage::

    python scrape_programs.py                      # build programs_index.json (index only)
    python scrape_programs.py --requirements --match "Computer Science"
    python scrape_programs.py --requirements --types major,minor --limit 50
    python scrape_programs.py --requirements --slugs computer-science-bs,mathematics-bs
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

# Reuse the proven helpers from the course scraper.
from scrape_courses import extract_course_codes, normalize_code

HERE = Path(__file__).resolve().parent
SCRAPE_DIR = HERE / "scrape"
SESSION_FILE = SCRAPE_DIR / "waf_session.json"
WAF_HELPER = SCRAPE_DIR / "waf_fetch.mjs"
CACHE_DIR = HERE / "cache" / "programs"
GENERATED_DIR = HERE / "webapp" / "programs" / "generated"
INDEX_PATH = HERE / "programs_index.json"

BASE = "https://catalog.purdue.edu"
DEFAULT_CATOID = 19
# Navigation pages that enumerate undergraduate programs (per the live catalog).
LIST_NAVOIDS = {
    "all": 25484,        # Undergraduate Programs List (majors + minors + certs + grad, ~960)
    "minors": 25481,     # Undergraduate Minors
    "certificates": 25483,
}

BACHELORS = {
    "BS", "BA", "BFA", "BSE", "BSEE", "BSCMPE", "BSCE", "BSME", "BSAAE", "BSABE",
    "BSBME", "BSCHE", "BSEEE", "BSIE", "BSMSE", "BSNE", "BSCNE", "BSEN", "BSF",
    "BSEED", "BSNUR", "BSPS", "BSAT", "BSHK", "BSW", "BMUS", "BPS",
}
GRADUATE_HINTS = ("MS", "MA", "PHD", "MFA", "MBA", "MSE", "MSED", "MENG", "DNP", "AUD", "EDD", "JD", "PHARMD", "DVM")


# ---------------------------------------------------------------------------
# WAF session (mint via headless Chrome, reuse via requests)
# ---------------------------------------------------------------------------


class Fetcher:
    """Cache-aware HTTP getter that transparently re-mints the WAF token."""

    def __init__(self, delay: float = 0.4, timeout: int = 30):
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self._load_session(mint_if_missing=True)

    def _load_session(self, mint_if_missing: bool = False, force_mint: bool = False) -> None:
        if force_mint or (mint_if_missing and not SESSION_FILE.exists()):
            self._mint()
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        self.session.headers.update({
            "User-Agent": data["userAgent"],
            "Cookie": data["cookie"],
            "Accept": "text/html,application/xhtml+xml",
        })

    def _mint(self) -> None:
        print("  [waf] minting aws-waf-token via headless Chrome…", file=sys.stderr)
        proc = subprocess.run(
            ["node", str(WAF_HELPER), "mint", "--out", str(SESSION_FILE)],
            cwd=str(SCRAPE_DIR), capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not SESSION_FILE.exists():
            raise RuntimeError(f"WAF mint failed: {proc.stderr.strip() or proc.stdout.strip()}")

    @staticmethod
    def _is_challenge(resp: requests.Response) -> bool:
        return (
            resp.status_code == 202
            or resp.headers.get("x-amzn-waf-action") == "challenge"
            or len(resp.text) < 2000
        )

    def get(self, url: str, cache_path: Path, refresh: bool = False) -> str | None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists() and not refresh:
            return cache_path.read_text(encoding="utf-8", errors="replace")
        for attempt in (1, 2):
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                print(f"  [fetch] error {url}: {exc}", file=sys.stderr)
                return None
            if self._is_challenge(resp):
                if attempt == 1:
                    self._load_session(force_mint=True)
                    continue
                print(f"  [fetch] still challenged after re-mint: {url}", file=sys.stderr)
                return None
            cache_path.write_text(resp.text, encoding="utf-8")
            time.sleep(self.delay)
            return resp.text
        return None


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    s = name.lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[,/():]", " ", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-+", "-", s)


def classify(name: str) -> tuple[str, str]:
    """Return (type, degree) from a program title using its suffix."""
    n = name.strip()
    if re.search(r"\bMinor\b", n):
        return "minor", "Minor"
    if re.search(r"\bCertificate\b", n):
        return "certificate", "Certificate"
    if "," in n:
        degree = n.rsplit(",", 1)[1].strip()
        token = re.sub(r"[^A-Z]", "", degree.upper().split()[0] if degree else "")
        if token in BACHELORS or (token.startswith("B") and len(token) <= 7):
            return "major", degree
        if token in GRADUATE_HINTS or token.startswith(("M", "PHD", "D", "ED")):
            return "graduate", degree
        return "other", degree
    return "other", ""


def parse_index(html: str, catoid: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, dict] = {}
    for a in soup.select("a[href*=preview_program]"):
        m = re.search(r"poid=(\d+)", a.get("href", ""))
        name = " ".join(a.get_text().split())
        if not m or not name:
            continue
        poid = m.group(1)
        if poid in out:
            continue
        ptype, degree = classify(name)
        out[poid] = {
            "poid": poid,
            "name": name,
            "type": ptype,
            "degree": degree,
            "slug": slugify(name),
            "source_url": f"{BASE}/preview_program.php?catoid={catoid}&poid={poid}",
        }
    return list(out.values())


# ---------------------------------------------------------------------------
# Program-page parsing
# ---------------------------------------------------------------------------

COURSE_LINE = re.compile(
    r"^([A-Z]{2,5})\s+(\d{5})\s*[-–]\s*(.*?)\s*Credit Hours?:\s*([\d.]+)", re.I
)
CREDIT_RANGE = re.compile(r"\(([\d.]+)\s*(?:[-–]\s*([\d.]+))?\s*credits?\)", re.I)
PLACEHOLDER_LINE = re.compile(r"^(.*?)\s*[-–]?\s*Credit Hours?:\s*([\d.]+)", re.I)

HEADING_LEVEL = {"h2": 2, "h3": 3, "h4": 4}
# Headings that are prose/policy, not requirements — skipped inside the requirements span.
NOTE_HEADINGS = (
    "civics literacy", "upper level requirement", "course requirements", "concentration note",
    "pre-requisite information", "world language", "critical course", "disclaimer",
    "gpa requirement", "grade requirement", "transfer credit", "pass/no pass", "pass no pass",
    "summer course", "curriculum and degree requirements for", "additional information",
    "honors requirement", "study abroad", "about the",
)
# Where the requirements section starts / ends.
REQ_START_RE = re.compile(r"(degree requirements|requirements for the (minor|certificate|program|major|concentration)|^requirements\b)", re.I)
STOP_RE = re.compile(r"(sample.*plan|\d-year plan|plan of study|pre-requisite information|disclaimer|pass/?\s*no\s*pass|transfer credit policy|world language courses|critical course|^notes?\b)", re.I)


def parse_course_text(text: str) -> dict | None:
    text = " ".join(text.replace("♠", "").replace("◆", "").split())
    m = COURSE_LINE.match(text)
    if m:
        subj, num, title, cr = m.groups()
        return {"code": normalize_code(f"{subj} {num}"), "title": title.strip(" -"), "credits": float(cr)}
    return None


def parse_slot_text(text: str) -> dict | None:
    text = " ".join(text.replace("♠", "").replace("◆", "").split())
    m = PLACEHOLDER_LINE.match(text)
    if m and m.group(1).strip():
        return {"label": m.group(1).strip(" -"), "credits": float(m.group(2))}
    if text:
        return {"label": text, "credits": None}
    return None


def credit_target(title: str) -> tuple[float | None, float | None]:
    m = CREDIT_RANGE.search(title)
    if not m:
        return (None, None)
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    return (lo, hi)


def _headings(soup: BeautifulSoup) -> list[Tag]:
    return [h for h in soup.find_all(["h2", "h3", "h4"]) if " ".join(h.get_text().split())]


def _li_until(start: Tag, stop: Tag | None):
    """Yield <li> tags in document order strictly between `start` and `stop` headings."""
    for el in start.next_elements:
        if el is stop:
            return
        if isinstance(el, Tag) and el.name == "li":
            yield el


def _ends_with_or(text: str) -> bool:
    return bool(re.search(r"\bor\s*$", text, re.I))


def _group_alternatives(rows: list[dict]) -> list[dict]:
    """Collapse runs of items whose source line ended in "or" into {"_alt": [...]} groups.

    Purdue lists alternatives inline (e.g. "MA 16100 … 5.00 or" then "MA 16500 …"), which
    are a pick-one, not all-required. Singles pass through unchanged.
    """
    out: list[dict] = []
    i = 0
    while i < len(rows):
        grp = [rows[i]]
        while rows[i].get("_or") and i + 1 < len(rows):
            i += 1
            grp.append(rows[i])
        i += 1
        clean = [{k: v for k, v in g.items() if k != "_or"} for g in grp]
        out.append({"_alt": clean} if len(clean) > 1 else clean[0])
    return out


def _courses_between(start: Tag, stop: Tag | None) -> list[dict]:
    """acalog course <li> items between two headings (with trailing-"or" flags)."""
    out: list[dict] = []
    for el in _li_until(start, stop):
        if "acalog-course" in (el.get("class") or []):
            text = el.get_text(" ", strip=True)
            c = parse_course_text(text)
            if c:
                c["_or"] = _ends_with_or(text)
                out.append(c)
    return out


def _items_between(start: Tag, stop: Tag | None) -> list[dict]:
    """All <li> (courses + plain selectives) between two headings — for sample plans."""
    raw: list[dict] = []
    for el in _li_until(start, stop):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        c = parse_course_text(text)
        if c:
            raw.append({**c, "_or": _ends_with_or(text)})
        else:
            slot = parse_slot_text(text)
            if slot and "credit hours" not in slot["label"].lower():
                raw.append({**slot, "slot": True, "_or": _ends_with_or(text)})
    items: list[dict] = []
    for g in _group_alternatives(raw):
        if "_alt" in g:
            head = dict(g["_alt"][0])
            head["alternatives"] = g["_alt"][1:]
            items.append(head)
        else:
            items.append(g)
    return items


def _build_tree(blocks: list[dict]) -> list[dict]:
    """Nest flat (level,node) blocks into a tree by heading level via a stack."""
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for blk in blocks:
        level, node = blk["level"], blk["node"]
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].setdefault("children", []).append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots


def _classify_node(node: dict) -> dict:
    """Turn a raw heading node into an engine requirement node."""
    title = node["_title"]
    low = title.lower()
    lo, hi = node["_credits"]
    courses = node.get("_courses", [])
    children = node.get("children", [])
    name = re.sub(r"\s*\([\d.\s–-]*credits?\)", "", title, flags=re.I).strip()
    base = {"name": name}
    if lo is not None:
        base["credit_target"] = lo if lo == hi else [lo, hi]
    is_choice = any(w in low for w in ("selective", "elective", "option", "choose", "select ", "additional"))

    if children:
        # container: all sub-requirements apply
        return {**base, "kind": "all_of", "children": [c for c in children]}
    if courses:
        grouped = _group_alternatives(courses)

        def alt_node(alt: list[dict]) -> dict:
            codes = [a["code"] for a in alt]
            return {"kind": "one_of", "name": " / ".join(codes), "courses": codes}

        # credit sum counts each alternative-group once
        course_sum = sum((g["_alt"][0] if "_alt" in g else g).get("credits") or 0 for g in grouped)
        pick = is_choice or (lo and "required" not in low and lo + 0.5 < course_sum)
        if not pick:
            singles = [g["code"] for g in grouped if "_alt" not in g]
            alts = [alt_node(g["_alt"]) for g in grouped if "_alt" in g]
            node_out = {**base, "kind": "all_of"}
            if singles:
                node_out["courses"] = singles
            if alts:
                node_out["children"] = alts
            return node_out
        options: list = [g["code"] if "_alt" not in g else alt_node(g["_alt"]) for g in grouped]
        node_out = {**base, "kind": "choose", "options": options}
        if lo:
            node_out["choose_credits"] = lo
        return node_out
    # no explicit courses -> placeholder credit bucket
    return {
        **base, "kind": "credits", "credits": lo or 0, "placeholder": True,
        "note": "Auto-extracted placeholder — fill with approved courses; verify with advising.",
    }


def parse_program(html: str, entry: dict, catoid: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = " ".join(h1.get_text().split()) if h1 else entry["name"]

    heads = _headings(soup)
    titles = [" ".join(h.get_text().split()) for h in heads]

    def find_idx(substr: str, start: int = 0) -> int | None:
        for i in range(start, len(titles)):
            if substr.lower() in titles[i].lower():
                return i
        return None

    total_credits = None
    for t in titles:
        m = re.match(r"(\d{2,3})\s+credits?\s+required", t, re.I)
        if m:
            total_credits = int(m.group(1))
            break

    req_start = next((i for i, t in enumerate(titles) if REQ_START_RE.search(t)), None)
    plan_idx = find_idx("Sample 4-Year Plan") or find_idx("Sample Plan") or find_idx("4-Year Plan")
    if plan_idx is not None:
        req_end = plan_idx
    elif req_start is not None:
        # no sample plan (typical for minors) — stop at the first trailing note/policy section
        req_end = next((i for i in range(req_start + 1, len(heads)) if STOP_RE.search(titles[i])), len(heads))
    else:
        req_end = len(heads)

    if total_credits is None and req_start is not None:
        lo, _ = credit_target(titles[req_start])  # minors: "Requirements for the Minor (15 credits)"
        if lo:
            total_credits = int(lo)

    # --- requirement tree -------------------------------------------------
    blocks: list[dict] = []
    if req_start is not None:
        for i in range(req_start + 1, req_end):
            title = titles[i]
            low = title.lower()
            if re.match(r"\d{2,3}\s+credits?\s+required", low):
                continue  # the total, captured above
            if any(h in low for h in NOTE_HEADINGS):
                continue
            stop = heads[i + 1] if i + 1 < len(heads) else None
            node = {
                "_title": title,
                "_credits": credit_target(title),
                "_courses": _courses_between(heads[i], stop),
                "level": HEADING_LEVEL.get(heads[i].name, 3),
            }
            blocks.append({"level": node["level"], "node": node})
    raw_tree = _build_tree([b for b in blocks])
    requirements = _prune([_finalize(n) for n in raw_tree])

    # --- official sample 4-year plan -------------------------------------
    recommended_sequence = []
    if plan_idx is not None:
        i = plan_idx + 1
        while i < len(heads):
            h = heads[i]
            t = titles[i]
            if HEADING_LEVEL.get(h.name) == 2:  # next major section ends the plan
                break
            if re.search(r"(Fall|Spring|Summer)", t) and re.search(r"(Year|Semester|\d)", t):
                stop = heads[i + 1] if i + 1 < len(heads) else None
                credit_hint = titles[i + 1] if (i + 1 < len(titles) and "credit" in titles[i + 1].lower()) else None
                recommended_sequence.append({
                    "term": t,
                    "credit_hint": credit_hint,
                    "items": _items_between(h, stop),
                })
            i += 1

    referenced = sorted({c for c in extract_course_codes(soup.get_text("\n")) })

    # code -> credits for every course listed anywhere on the page (used by the scheduler)
    course_credits: dict[str, float] = {}
    for li in soup.select("li.acalog-course"):
        c = parse_course_text(li.get_text(" ", strip=True))
        if c:
            course_credits.setdefault(c["code"], c["credits"])

    return {
        "id": entry["slug"],
        "poid": entry["poid"],
        "name": name,
        "degree": entry.get("degree"),
        "type": entry.get("type"),
        "catalog_year": f"catoid-{catoid}",
        "total_credits": total_credits,
        "source_url": entry["source_url"],
        "verified": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "course_credits": course_credits,
        "notes": [
            "Auto-generated from the Purdue catalog and NOT verified. Requirement structure is a "
            "best-effort parse — confirm against myPurduePlan / advising before relying on it.",
        ],
        "requirements": requirements,
        "recommended_sequence": recommended_sequence,
        "referenced_courses": referenced,
    }


def _finalize(node: dict) -> dict:
    """Recursively classify a raw heading node (children already attached)."""
    if "children" in node:
        node["children"] = [_finalize(c) for c in node["children"]]
    return _classify_node(node)


def _prune(nodes: list[dict]) -> list[dict]:
    """Drop empty label/note nodes (no courses, no children, no positive credit target).

    These are section headers like "Curriculum and Degree Requirements for College of
    Science" or leftover policy prose. A 0-credit placeholder would otherwise be
    permanently unsatisfiable in the engine and wrongly block the whole program.
    """
    out: list[dict] = []
    for n in nodes:
        if "children" in n:
            n["children"] = _prune(n["children"])
        has_content = bool(n.get("courses") or n.get("options") or n.get("children"))
        target = n.get("choose_credits") or n.get("credits") or 0
        if not has_content and not (target and target > 0):
            continue
        out.append(n)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_index(fetcher: Fetcher, catoid: int, refresh: bool) -> list[dict]:
    merged: dict[str, dict] = {}
    for label, navoid in LIST_NAVOIDS.items():
        url = f"{BASE}/content.php?catoid={catoid}&navoid={navoid}"
        html = fetcher.get(url, CACHE_DIR / f"index_{catoid}_{navoid}.html", refresh)
        if not html:
            print(f"  [index] {label}: fetch failed", file=sys.stderr)
            continue
        entries = parse_index(html, catoid)
        for e in entries:
            merged.setdefault(e["poid"], e)
        print(f"  [index] {label} (navoid={navoid}): {len(entries)} programs")
    return list(merged.values())


def write_index(programs: list[dict], catoid: int) -> None:
    from collections import Counter
    by_type = Counter(p["type"] for p in programs)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catoid": catoid,
        "source": f"{BASE}/content.php?catoid={catoid}",
        "counts": dict(by_type),
        "programs": sorted(programs, key=lambda p: (p["type"], p["name"])),
    }
    INDEX_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {INDEX_PATH.name}: {len(programs)} programs {dict(by_type)}")


def scrape_requirements(fetcher: Fetcher, targets: list[dict], catoid: int, refresh: bool) -> None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for i, entry in enumerate(targets, 1):
        url = entry["source_url"]
        html = fetcher.get(url, CACHE_DIR / f"prog_{catoid}_{entry['poid']}.html", refresh)
        if not html:
            fail += 1
            print(f"  [{i}/{len(targets)}] {entry['slug']}: FETCH FAILED")
            continue
        try:
            program = parse_program(html, entry, catoid)
            (GENERATED_DIR / f"{entry['slug']}.json").write_text(
                json.dumps(program, indent=2), encoding="utf-8"
            )
            nreq = len(program["requirements"])
            nseq = len(program["recommended_sequence"])
            ncourse = len(program["referenced_courses"])
            ok += 1
            print(f"  [{i}/{len(targets)}] {entry['slug']}: {nreq} req groups, "
                  f"{nseq} plan terms, {ncourse} courses, total={program['total_credits']}")
        except Exception as exc:  # never fatal
            fail += 1
            print(f"  [{i}/{len(targets)}] {entry['slug']}: PARSE ERROR {exc}")
    print(f"\nRequirements: {ok} ok, {fail} failed -> {GENERATED_DIR}")


def select_targets(programs: list[dict], args) -> list[dict]:
    pool = programs
    if args.types:
        wanted = {t.strip() for t in args.types.split(",")}
        pool = [p for p in pool if p["type"] in wanted]
    if args.match:
        ml = args.match.lower()
        pool = [p for p in pool if ml in p["name"].lower()]
    if args.slugs:
        slugs = {s.strip() for s in args.slugs.split(",")}
        pool = [p for p in pool if p["slug"] in slugs]
    if args.limit:
        pool = pool[: args.limit]
    return pool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catoid", type=int, default=DEFAULT_CATOID)
    parser.add_argument("--refresh", action="store_true", help="ignore cache, re-fetch")
    parser.add_argument("--requirements", action="store_true", help="also scrape per-program requirement trees")
    parser.add_argument("--types", help="comma list to filter requirements scrape: major,minor,certificate")
    parser.add_argument("--match", help="substring filter on program name")
    parser.add_argument("--slugs", help="comma list of exact slugs to scrape")
    parser.add_argument("--limit", type=int, help="cap number of programs in requirements scrape")
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args(argv)

    fetcher = Fetcher(delay=args.delay)
    print("Building program index…")
    programs = build_index(fetcher, args.catoid, args.refresh)
    if not programs:
        print("No programs found — aborting.", file=sys.stderr)
        return 1
    write_index(programs, args.catoid)

    if args.requirements:
        targets = select_targets(programs, args)
        print(f"\nScraping requirements for {len(targets)} programs…")
        scrape_requirements(fetcher, targets, args.catoid, args.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
