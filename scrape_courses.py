#!/usr/bin/env python3
"""Scrape Purdue course catalog detail pages into course_catalog.json.

The scraper is intentionally conservative:
- one URL per subject/number/term
- local HTML cache by default
- raw HTML and normalized raw text are stored as fallbacks
- failures are logged and do not stop the run
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup


DEFAULT_TERM = "202710"
BASE_URL = "https://selfservice.mypurdue.purdue.edu/prod/bwckctlg.p_disp_course_detail"
HEADERS = {
    "User-Agent": "degree-planner/0.1 (+local academic planning script)"
}


@dataclass
class CourseRequest:
    subject: str
    number: str
    notes: str = ""

    @property
    def code(self) -> str:
        return normalize_code(f"{self.subject} {self.number}")


def normalize_code(value: str) -> str:
    value = " ".join(str(value).strip().upper().replace("-", " ").split())
    match = re.match(r"^([A-Z]+)\s*([0-9][0-9A-Z]{2,5})$", value)
    if match:
        subject, number = match.groups()
        if number.isdigit() and len(number) < 5:
            number = number.zfill(5)
        return f"{subject} {number}"
    return value


def course_url(term: str, subject: str, number: str) -> str:
    query = urlencode(
        {
            "cat_term_in": term,
            "subj_code_in": subject,
            "crse_numb_in": number,
        }
    )
    return f"{BASE_URL}?{query}"


def read_course_requests(path: Path) -> list[CourseRequest]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"subject", "number"}
        if not required.issubset(reader.fieldnames or set()):
            raise ValueError(f"{path} must contain subject and number columns")
        rows = []
        for row in reader:
            subject = (row.get("subject") or "").strip().upper()
            number = (row.get("number") or "").strip().upper()
            if not subject or not number:
                continue
            rows.append(CourseRequest(subject=subject, number=number, notes=(row.get("notes") or "").strip()))
        return rows


def fetch_html(
    session: requests.Session,
    request: CourseRequest,
    term: str,
    cache_dir: Path,
    refresh: bool,
    timeout: int,
) -> tuple[str | None, int | None, str, bool, str | None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{term}_{request.subject}_{request.number}.html"
    url = course_url(term, request.subject, request.number)

    if cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8", errors="replace"), None, url, True, None

    try:
        response = session.get(url, timeout=timeout)
        status = response.status_code
        response.raise_for_status()
    except requests.RequestException as exc:
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8", errors="replace"), None, url, True, str(exc)
        return None, None, url, False, str(exc)

    html = response.text
    cache_path.write_text(html, encoding="utf-8")
    return html, status, url, False, None


def text_lines(soup: BeautifulSoup) -> list[str]:
    return [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]


def compact_text(lines: Iterable[str]) -> str:
    return "\n".join(lines)


def find_term_label(lines: list[str]) -> str | None:
    for line in lines:
        if re.match(r"^(Fall|Spring|Summer)\s+\d{4}$", line):
            return line
    return None


def parse_course_heading(lines: list[str]) -> tuple[str | None, str | None, str | None]:
    for line in lines:
        match = re.match(r"^([A-Z]+)\s+([0-9][0-9A-Z]{2,5})\s+-\s+(.+)$", line)
        if match:
            subject, number, title = match.groups()
            if number.isdigit() and len(number) < 5:
                number = number.zfill(5)
            return subject, number, title.strip()
    return None, None, None


def parse_credits_and_description(lines: list[str]) -> tuple[dict[str, float | str | None], str | None]:
    credits = {"min": None, "max": None, "raw": None}
    description = None
    for line in lines:
        if "Credit Hours:" not in line:
            continue
        credits["raw"] = line
        credit_match = re.search(
            r"Credit Hours:\s*([0-9]+(?:\.[0-9]+)?)(?:\s*(?:TO|-)\s*([0-9]+(?:\.[0-9]+)?))?",
            line,
            flags=re.IGNORECASE,
        )
        if credit_match:
            first = float(credit_match.group(1))
            second = float(credit_match.group(2)) if credit_match.group(2) else first
            credits["min"] = first
            credits["max"] = second

        after = re.split(r"Credit Hours:\s*[0-9.]+(?:\s*(?:TO|-)\s*[0-9.]+)?\.?", line, maxsplit=1, flags=re.IGNORECASE)
        if len(after) == 2:
            description = after[1].strip()
        break
    return credits, description or None


SECTION_HEADINGS = {
    "Levels:",
    "Schedule Types:",
    "All Sections for this Course",
    "Offered By:",
    "Department:",
    "Course Attributes:",
    "May be offered at any of the following campuses:",
    "Learning Outcomes:",
    "Restrictions:",
    "Prerequisites:",
    "Corequisites:",
    "Return to Previous",
    "New Search",
}


def section_after(lines: list[str], heading: str) -> str | None:
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return None
    section: list[str] = []
    for line in lines[start:]:
        if line in SECTION_HEADINGS:
            break
        section.append(line)
    return "\n".join(section).strip() or None


def parse_list_section(lines: list[str], heading: str) -> list[str]:
    section = section_after(lines, heading)
    if not section:
        return []
    values = []
    for line in section.splitlines():
        cleaned = line.strip(" ,")
        if cleaned:
            values.append(cleaned)
    return values


def parse_single_value(lines: list[str], heading: str) -> str | None:
    try:
        idx = lines.index(heading)
    except ValueError:
        return None
    if idx + 1 < len(lines):
        candidate = lines[idx + 1].strip()
        if candidate and candidate not in SECTION_HEADINGS:
            return candidate
    return None


def extract_course_codes(text: str | None) -> list[str]:
    if not text:
        return []
    codes: set[str] = set()

    # Banner prerequisite text often renders as:
    # Course or Test:
    # CS
    # 18000
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines[:-1]):
        if re.fullmatch(r"[A-Z]{2,5}", line) and re.fullmatch(r"[0-9][0-9A-Z]{2,5}", lines[idx + 1]):
            codes.add(normalize_code(f"{line} {lines[idx + 1]}"))

    for subject, number in re.findall(r"\b([A-Z]{2,5})\s+([0-9][0-9A-Z]{2,5})\b", text):
        codes.add(normalize_code(f"{subject} {number}"))

    return sorted(codes)


def parse_course(html: str, request: CourseRequest, term: str, url: str, http_status: int | None, from_cache: bool, fetch_error: str | None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    lines = text_lines(soup)
    raw_text = compact_text(lines)
    not_found = "No course to display" in raw_text
    rate_limited = "too many requests" in raw_text.lower()

    parsed_subject, parsed_number, title = parse_course_heading(lines)
    credits, description = parse_credits_and_description(lines)
    restrictions = section_after(lines, "Restrictions:")
    prerequisites = section_after(lines, "Prerequisites:")
    corequisites = section_after(lines, "Corequisites:")

    status = "rate_limited" if rate_limited else ("not_found" if not_found else "ok")
    if fetch_error and not from_cache:
        status = "error"

    parsed_code = normalize_code(f"{parsed_subject} {parsed_number}") if parsed_subject and parsed_number else None
    requested_code = request.code

    prerequisite_courses = [code for code in extract_course_codes(prerequisites) if code != (parsed_code or requested_code)]
    corequisite_courses = [code for code in extract_course_codes(corequisites) if code != (parsed_code or requested_code)]

    return {
        "status": status,
        "requested_code": requested_code,
        "code": parsed_code or requested_code,
        "requested_subject": request.subject,
        "requested_number": request.number,
        "subject": parsed_subject,
        "number": parsed_number,
        "title": title,
        "credits": credits,
        "description": description,
        "queried_term": term,
        "term_label": find_term_label(lines),
        "url": url,
        "http_status": http_status,
        "from_cache": from_cache,
        "fetch_error": fetch_error,
        "notes": request.notes,
        "levels": parse_list_section(lines, "Levels:"),
        "schedule_types": parse_list_section(lines, "Schedule Types:"),
        "offered_by": parse_single_value(lines, "Offered By:"),
        "department": parse_single_value(lines, "Department:"),
        "attributes": parse_list_section(lines, "Course Attributes:"),
        "campuses": parse_list_section(lines, "May be offered at any of the following campuses:"),
        "restrictions_text": restrictions,
        "prerequisites_text": prerequisites,
        "corequisites_text": corequisites,
        "prerequisite_courses": prerequisite_courses,
        "corequisite_courses": corequisite_courses,
        "raw_text": raw_text,
        "raw_html": html,
    }


def scrape(args: argparse.Namespace) -> dict:
    input_path = Path(args.input)
    output_path = Path(args.output)
    cache_dir = Path(args.cache_dir)
    requests_to_make = read_course_requests(input_path)

    session = requests.Session()
    session.headers.update(HEADERS)

    # Merge with whatever's already in the output (on-demand scrapes add courses that aren't in
    # the CSV; a plain rewrite would drop them). --no-merge restores the old overwrite behavior.
    existing_courses: dict = {}
    if output_path.exists() and not getattr(args, "no_merge", False):
        try:
            existing_courses = json.loads(output_path.read_text(encoding="utf-8")).get("courses", {})
        except (json.JSONDecodeError, OSError):
            existing_courses = {}

    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "term": args.term,
        "source_base_url": BASE_URL,
        "input": str(input_path),
        "courses": dict(existing_courses),
        "failures": [],
    }

    for index, course_request in enumerate(requests_to_make, start=1):
        html, http_status, url, from_cache, fetch_error = fetch_html(
            session=session,
            request=course_request,
            term=args.term,
            cache_dir=cache_dir,
            refresh=args.refresh,
            timeout=args.timeout,
        )

        if html is None:
            record = {
                "status": "error",
                "requested_code": course_request.code,
                "code": course_request.code,
                "requested_subject": course_request.subject,
                "requested_number": course_request.number,
                "queried_term": args.term,
                "url": url,
                "http_status": http_status,
                "fetch_error": fetch_error,
                "notes": course_request.notes,
                "raw_text": None,
                "raw_html": None,
            }
        else:
            record = parse_course(
                html=html,
                request=course_request,
                term=args.term,
                url=url,
                http_status=http_status,
                from_cache=from_cache,
                fetch_error=fetch_error,
            )

        catalog["courses"][course_request.code] = record
        if record["status"] != "ok":
            catalog["failures"].append(
                {
                    "code": course_request.code,
                    "status": record["status"],
                    "url": url,
                    "error": record.get("fetch_error"),
                }
            )

        status_label = "cache" if record.get("from_cache") else record["status"]
        print(f"[{index:02d}/{len(requests_to_make):02d}] {course_request.code}: {status_label}")
        if args.delay and index < len(requests_to_make) and not record.get("from_cache"):
            time.sleep(args.delay)

    output_path.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")
    return catalog


def print_summary(catalog: dict) -> None:
    courses = catalog["courses"]
    ok = [code for code, record in courses.items() if record.get("status") == "ok"]
    failed = [code for code, record in courses.items() if record.get("status") != "ok"]
    print()
    print("Scrape summary")
    print(f"  Term: {catalog['term']}")
    print(f"  Courses requested: {len(courses)}")
    print(f"  Successful: {len(ok)}")
    print(f"  Failed/not found: {len(failed)}")
    if failed:
        print("  Failed/not found codes:")
        for code in failed:
            record = courses[code]
            reason = record.get("fetch_error") or record.get("status")
            print(f"    - {code}: {reason}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--term", default=DEFAULT_TERM, help=f"Catalog term code, default {DEFAULT_TERM}")
    parser.add_argument("--input", default="courses_to_scrape.csv", help="CSV with subject,number columns")
    parser.add_argument("--output", default="course_catalog.json", help="Output JSON path")
    parser.add_argument("--cache-dir", default="cache", help="Directory for raw HTML cache")
    parser.add_argument("--refresh", action="store_true", help="Ignore existing cache and re-fetch")
    parser.add_argument("--no-merge", action="store_true", help="Overwrite output instead of merging into existing courses")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between network requests")
    parser.add_argument("--timeout", type=int, default=25, help="HTTP timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        catalog = scrape(args)
    except Exception as exc:  # pragma: no cover - top-level CLI guard
        print(f"scrape failed: {exc}", file=sys.stderr)
        return 1
    print_summary(catalog)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
