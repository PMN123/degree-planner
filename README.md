# Boiler Degree Planner

A **code-backed Purdue graduation planner**. Pick any combination of majors and
minors, drag your courses into semesters, and get a live audit: degree
requirement coverage, prerequisite checks (parsed from the real catalog),
semester loads, credit totals, and cross-degree course overlap.

It runs on the **Python standard library only** — no Flask, no npm, no build
step. `python webapp/server.py` and open your browser.

![Boiler Degree Planner — dark theme](docs/screenshot-dark.png)

> ⚠️ The bundled requirement data is an encoded **starter**. Always verify against
> [myPurduePlan](https://www.purdue.edu/registrar/) and your academic advisor
> before registering. This repository ships **no personal academic records**.

---

## Why it's different

Most planners are spreadsheets. This one actually *understands* the rules:

- **Any major, any plan.** Each program is a declarative JSON file describing a
  tree of requirements (`all_of`, `one_of`, `choose N`, credit buckets, and
  selectable concentration tracks). Adding a new major is one JSON file — **no
  code changes**. A generic engine evaluates them all.
- **Real prerequisite logic.** Prereqs are parsed from scraped Purdue catalog
  text with full `AND`/`OR`/parentheses, Banner rule blocks, and
  concurrent-enrollment handling — then checked term-by-term against your plan.
- **On-demand scraping.** Type a course that isn't cached and the app fetches it
  live from the Purdue Self-Service catalog and remembers it.
- **Built for sharing.** Deep links (`?demo=1`, `?open=<program>`) and a
  black-and-gold / Purdue-white theme toggle make it demo-ready.

| Light theme | Requirement detail |
| --- | --- |
| ![light](docs/screenshot-light.png) | ![requirements](docs/screenshot-requirements.png) |

---

## Quick start (web app)

```bash
git clone https://github.com/PMN123/degree-planner.git
cd degree-planner

# The web app itself needs nothing beyond Python 3.10+.
# (requests + beautifulsoup4 are only needed for live/on-demand scraping.)
pip install -r requirements.txt        # optional, enables scraping

python webapp/server.py                # -> http://127.0.0.1:8000
```

Then open <http://127.0.0.1:8000>. Click **Load example** for an instant CS +
Math demo, pick your own degrees from the pills, and start adding courses. Your
plan auto-saves to the browser (`localStorage`) — nothing is sent anywhere.

Useful URLs:

- `http://127.0.0.1:8000/?demo=1` — open with the example plan loaded
- `http://127.0.0.1:8000/?theme=light` — force light mode
- `http://127.0.0.1:8000/?demo=1&open=computer-science-bs` — open the CS requirement breakdown

---

## Adding a program (any major)

Drop a JSON file in [`webapp/programs/`](webapp/programs/). The server picks it up
on restart. Shared building blocks (calculus sequence, university core) live in
[`webapp/programs/_shared.json`](webapp/programs/_shared.json) and are pulled in
with `{ "$include": "name" }`.

```jsonc
{
  "id": "computer-science-bs",
  "name": "Computer Science",
  "degree": "BS",
  "type": "major",
  "college": "College of Science",
  "total_credits": 120,
  "requirements": [
    { "id": "core", "name": "CS Core", "kind": "all_of",
      "courses": ["CS 18000", "CS 18200", "CS 24000", "CS 25000", "CS 25100", "CS 25200"] },

    { "id": "track", "name": "Concentration", "kind": "track_select", "choose": 1,
      "tracks": [ /* each track is an all_of of required + choose nodes */ ] },

    { "id": "selectives", "name": "CS Selectives", "kind": "choose", "choose": 3,
      "constraints": { "max_by_subject": { "MA": 1 } },
      "options": ["CS 31400", "CS 44800", "CS 48300", "MA 35301"] },

    { "$include": "university_core_science" }
  ]
}
```

Requirement node kinds:

| `kind` | meaning |
| --- | --- |
| `all_of` | every listed course / child node is required |
| `one_of` | at least one course / child node satisfies it |
| `choose` | pick `choose` N (or `choose_credits` N) of `options`; supports `constraints.max_by_subject` |
| `credits` | accumulate `credits` from courses matching a `match` filter (or a `placeholder` bucket) |
| `track_select` | choose `choose` of `tracks` (each an `all_of`-style node) |

Starter programs included: Computer Science (BS + minor), Mathematics, Statistics
(Math Emphasis), Data Science, Artificial Intelligence, Computer Engineering, and
the Finance minor — spanning the Colleges of Science, Engineering, and the Daniels
School of Business.

---

## How it fits together

```
webapp/
├── server.py          stdlib HTTP server — JSON API + static SPA, no dependencies
├── engine.py          generic, major-agnostic requirement evaluator
├── catalog.py         catalog search / credits / prereq audit + on-demand scrape
├── programs_store.py  loads programs/*.json and resolves $include fragments
├── programs/          one JSON per major/minor  ← add majors here
└── static/            the single-page UI (index.html · styles.css · app.js)

audit_plan.py          structured prerequisite & restriction parser (reused by the web app)
scrape_courses.py      cache-aware Purdue Self-Service catalog scraper
course_catalog.json    seed scraped catalog (grows as you fetch on demand)
```

### JSON API

| Method & path | Purpose |
| --- | --- |
| `GET /api/meta` | catalog size, term, program count |
| `GET /api/programs` | list available programs |
| `GET /api/programs/{id}` | full requirement tree |
| `GET /api/courses/search?q=` | search the catalog |
| `GET /api/courses/{code}` | course detail (scrapes if missing) |
| `POST /api/courses/ensure` | `{"code": "..."}` fetch a course on demand |
| `POST /api/audit` | audit a posted plan against selected programs |

---

## Command-line tools (optional)

The original scripts still work for a file-based workflow:

```bash
pip install -r requirements.txt

# 1. scrape the catalog (cache-aware; cheap to re-run)
python scrape_courses.py

# 2. audit an example plan (ships with no personal data)
python audit_plan.py --plan examples/plan.example.yaml \
                     --completed examples/completed.example.json

# 3. export a workbook
python export_simple_plan_spreadsheet.py --plan examples/plan.example.yaml \
                     --output plan.xlsx
```

`requirements/*.json` hold the encoded rules used by the legacy `audit_plan.py`
auditor; the web app uses the newer generic schema in `webapp/programs/`.

---

## Notes & limits

- Requirement data is hand-encoded and may lag the live catalog — **verify with advising**.
- The seed catalog covers ~100 common CS/Math/Stat/Finance/Engineering courses;
  anything else is pulled on demand the first time you search for it.
- College-of-Science / university-core categories are modeled as credit
  placeholders until a real degree audit fills them in.
- Plans live only in your browser. Nothing is uploaded.

## License

[MIT](LICENSE) — built as a student project, shared for the Purdue community.
