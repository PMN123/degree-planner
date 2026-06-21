#!/usr/bin/env python3
"""Export any local plan YAML into a lightweight XLSX workbook."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from export_plan_spreadsheet import normalize_code, row_style_for_code, write_xlsx


ROOT = Path(__file__).resolve().parent


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def planned_courses(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for semester in plan.get("semesters", []):
        for position, course in enumerate(semester.get("courses", []), start=1):
            row = dict(course)
            row["term"] = semester["term"]
            row["position"] = position
            row["code"] = normalize_code(row["code"])
            rows.append(row)
    return rows


def listed_courses(plan: dict[str, Any], key: str, source_label: str) -> list[dict[str, Any]]:
    rows = []
    for position, course in enumerate(plan.get(key, []), start=1):
        row = dict(course)
        row["term"] = course.get("term", source_label)
        row["position"] = position
        row["code"] = normalize_code(row["code"])
        row["source"] = source_label
        rows.append(row)
    return rows


def make_workbook(plan_path: Path, output_path: Path) -> None:
    plan = load_yaml(plan_path)
    courses = planned_courses(plan)
    completed = listed_courses(plan, "completed_courses", "Completed before plan")
    outside = listed_courses(plan, "outside_six_semesters", "Outside six-semester plan")
    all_courses = completed + outside + courses

    summary_rows = [
        ["Metric", "Value"],
        ["Target graduation", plan.get("target_graduation", "")],
        ["Total planned credits", plan.get("total_planned_credits", "")],
        ["Completed credits assumed", plan.get("completed_credits_assumed", "")],
        ["Outside-plan credits", plan.get("outside_plan_credits", "")],
        ["Total credits with plan", plan.get("total_credits_with_plan", "")],
        ["Programs", "; ".join(plan.get("student_profile", {}).get("programs", []))],
        ["Majors", "; ".join(plan.get("student_profile", {}).get("majors", []))],
        ["Catalog sources", "; ".join(f"{key}: {value}" for key, value in plan.get("catalog_sources", {}).items())],
    ]
    for semester in plan.get("semesters", []):
        summary_rows.append([f"{semester['term']} credits", semester.get("credits", "")])
    for index, assumption in enumerate(plan.get("assumptions", []), start=1):
        summary_rows.append([f"Assumption {index}", assumption])

    semester_rows = [["Term", "Course", "Course name", "Credits", "Satisfies", "Notes"]]
    semester_styles = {}
    for course in courses:
        notes = []
        if course.get("original_code"):
            notes.append(f"Original code: {course['original_code']}")
        notes.extend(course.get("notes", []))
        semester_rows.append(
            [
                course["term"],
                course["code"],
                course.get("title", ""),
                course.get("credits", ""),
                "; ".join(course.get("satisfies", [])),
                "\n".join(notes),
            ]
        )
        semester_styles[len(semester_rows)] = row_style_for_code(course["code"])

    completed_rows = [["Source", "Term", "Course", "Course name", "Credits", "Satisfies", "Notes"]]
    completed_styles = {}
    for course in completed + outside:
        notes = []
        notes.extend(course.get("notes", []))
        completed_rows.append(
            [
                course.get("source", ""),
                course.get("term", ""),
                course["code"],
                course.get("title", ""),
                course.get("credits", ""),
                "; ".join(course.get("satisfies", [])),
                "\n".join(notes),
            ]
        )
        completed_styles[len(completed_rows)] = row_style_for_code(course["code"])

    flow_columns = [semester["term"] for semester in plan.get("semesters", [])]
    flow_by_term: dict[str, list[str]] = defaultdict(list)
    for course in courses:
        flow_by_term[course["term"]].append(
            f"{course['code']}\n{course.get('title', '')}\n{course.get('credits', '')} credits\n"
            f"{'; '.join(course.get('satisfies', []))}"
        )
    max_rows = max((len(flow_by_term[term]) for term in flow_columns), default=0)
    flow_rows = [flow_columns]
    for idx in range(max_rows):
        flow_rows.append([flow_by_term[term][idx] if idx < len(flow_by_term[term]) else "" for term in flow_columns])

    coverage_rows = [["Requirement / degree element", "Courses"]]
    coverage: defaultdict[str, list[str]] = defaultdict(list)
    for course in all_courses:
        label = f"{course['code']} ({course['term']})"
        for item in course.get("satisfies", []):
            coverage[item].append(label)
    for requirement, labels in sorted(coverage.items()):
        coverage_rows.append([requirement, "; ".join(labels)])

    sheets = [
        {"name": "Summary", "rows": summary_rows, "widths": [28, 100]},
        {
            "name": "Semester Plan",
            "rows": semester_rows,
            "widths": [18, 18, 44, 10, 72, 48],
            "row_styles": semester_styles,
        },
        {
            "name": "Completed Outside Plan",
            "rows": completed_rows,
            "widths": [24, 18, 18, 44, 10, 72, 48],
            "row_styles": completed_styles,
        },
        {"name": "Flow Layout", "rows": flow_rows, "widths": [34] * len(flow_columns)},
        {"name": "Requirement Coverage", "rows": coverage_rows, "widths": [52, 120]},
    ]
    write_xlsx(output_path, sheets)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="plan_ai_chinese.yaml")
    parser.add_argument("--output", default="ai_chinese_plan.xlsx")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        plan_path = ROOT / plan_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path

    make_workbook(plan_path, output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
