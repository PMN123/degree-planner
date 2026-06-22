#!/usr/bin/env python3
"""Export generated plan variants into a navigable XLSX workbook."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from export_plan_spreadsheet import write_xlsx


ROOT = Path(__file__).resolve().parent

QUICK_PICK_IDS = [
    "cs_algorithms",
    "cs_algorithms_math",
    "cs_algorithms_statistics",
    "cs_algorithms_math_statistics",
    "cs_algorithms_math_finance",
    "cs_algorithms_math_statistics_finance",
    "cs_algorithms_machine_intelligence_cse_math",
    "cs_algorithms_machine_intelligence_cse_math_statistics_finance",
]

TERM_ORDER = ["Fall 2026", "Spring 2027", "Fall 2027", "Spring 2028", "Fall 2028", "Spring 2029"]


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def fmt_credits(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return str(int(number)) if abs(number - int(number)) < 0.001 else f"{number:.1f}"


def term_band_style(term: str) -> int:
    try:
        index = TERM_ORDER.index(term)
    except ValueError:
        index = 0
    return 2 if index % 2 == 0 else 3


def formula(text: str, cached: str = "") -> dict[str, str]:
    return {"formula": text, "cached": cached}


def excel_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def link_to(sheet: str, cell: str, label: str) -> dict[str, str]:
    target = f"#'{sheet}'!{cell}"
    return formula(f"HYPERLINK({excel_string(target)},{excel_string(label)})", label)


def variant_label(variant: dict[str, Any]) -> str:
    extras = ", ".join(variant.get("extras") or []) or "no add-ons"
    tracks = ", ".join(variant.get("cs_tracks") or [])
    return f"{tracks} + {extras}"


def faster_21_label(variant: dict[str, Any]) -> str:
    if variant.get("plan_kind") == "21-credit faster option":
        return ""
    overload = variant.get("overload_21") or {}
    if overload and overload.get("target_graduation") != variant.get("target_graduation"):
        return f"{overload['target_graduation']} at max {fmt_credits(overload['max_semester_credits'])}"
    return ""


def is_faster_21_option(variant: dict[str, Any]) -> bool:
    overload = variant.get("overload_21") or {}
    return bool(overload and overload.get("target_graduation") != variant.get("target_graduation"))


def standard_plan(variant: dict[str, Any]) -> dict[str, Any]:
    plan = dict(variant)
    plan["plan_kind"] = "Standard <=20"
    plan["base_variant_id"] = variant["id"]
    return plan


def overload_plan(variant: dict[str, Any]) -> dict[str, Any]:
    overload = variant["overload_21"]
    plan = dict(variant)
    plan.update(
        {
            "id": f"{variant['id']}__21_credit",
            "base_variant_id": variant["id"],
            "plan_kind": "21-credit faster option",
            "target_graduation": overload.get("target_graduation", variant.get("target_graduation")),
            "semester_cap": overload.get("semester_cap", 21),
            "max_semester_credits": overload.get("max_semester_credits", variant.get("max_semester_credits")),
            "planned_credits": overload.get("planned_credits", variant.get("planned_credits")),
            "placeholder_credits": overload.get("placeholder_credits", variant.get("placeholder_credits")),
            "semesters": overload.get("semesters", []),
            "balanced_semesters": overload.get("balanced_semesters", []),
            "balanced": overload.get("balanced", {}),
            "validation": overload.get("validation", variant.get("validation", {})),
            "balanced_validation": overload.get("balanced_validation", variant.get("balanced_validation", {})),
            "notes": (variant.get("notes") or [])
            + [
                f"21-credit faster option for {variant['id']}. Use this only if one 21-credit semester is acceptable."
            ],
        }
    )
    return plan


def selectable_plans(data: dict[str, Any]) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for variant in data.get("variants", []):
        plans.append(standard_plan(variant))
        if is_faster_21_option(variant):
            plans.append(overload_plan(variant))
    return plans


def build_plan_rows(data: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    rows: list[list[Any]] = []
    row_styles: dict[int, int] = {}
    anchors: dict[str, int] = {}

    for variant in selectable_plans(data):
        anchors[variant["id"]] = len(rows) + 1
        rows.append(
            [
                variant["id"],
                variant.get("plan_kind", ""),
                variant_label(variant),
                f"Target: {variant.get('target_graduation', '')}",
                f"Max load: {fmt_credits(variant.get('max_semester_credits'))}",
                link_to("Start Here", "A1", "Back to picker"),
            ]
        )
        row_styles[len(rows)] = 1

        rows.append(
            [
                "CS tracks",
                ", ".join(variant.get("cs_tracks") or []),
                "Add-ons",
                ", ".join(variant.get("extras") or []) or "none",
                "21-credit faster option",
                faster_21_label(variant) or "none",
            ]
        )
        rows.append(
            [
                "Term",
                "Term credits",
                "Course",
                "Course name",
                "Credits",
                "Notes",
            ]
        )
        row_styles[len(rows)] = 1

        notes = "; ".join(variant.get("notes") or [])
        for semester in variant.get("semesters", []):
            for course in semester.get("courses", []):
                rows.append(
                    [
                        semester.get("term", ""),
                        semester.get("credits", ""),
                        course.get("code", ""),
                        course.get("title", ""),
                        course.get("credits", ""),
                        notes,
                    ]
                )
                row_styles[len(rows)] = term_band_style(semester.get("term", ""))
        rows.append(["", "", "", "", "", ""])

    sheet = {
        "name": "Plans",
        "rows": rows,
        "widths": [18, 14, 22, 44, 12, 96],
        "row_styles": row_styles,
    }
    return anchors, sheet


def build_balanced_plan_rows(data: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    rows: list[list[Any]] = []
    row_styles: dict[int, int] = {}
    anchors: dict[str, int] = {}

    for variant in selectable_plans(data):
        anchors[variant["id"]] = len(rows) + 1
        balanced = variant.get("balanced") or {}
        validation = variant.get("balanced_validation") or {}
        rows.append(
            [
                variant["id"],
                variant.get("plan_kind", ""),
                variant_label(variant),
                f"Target: {variant.get('target_graduation', '')}",
                f"Balanced max: {fmt_credits(balanced.get('max_semester_credits'))}",
                f"Spread: {fmt_credits(balanced.get('load_spread'))}",
                "OK"
                if all(
                    validation.get(key)
                    for key in [
                        "target_tracks_ok",
                        "math_ok",
                        "statistics_ok",
                        "finance_ok",
                        "prerequisites_ok",
                        "restriction_checks_ok",
                    ]
                )
                else "CHECK",
            ]
        )
        row_styles[len(rows)] = 1

        rows.append(
            [
                "Term",
                "Term credits",
                "Course",
                "Course name",
                "Credits",
                "Notes",
                "",
            ]
        )
        row_styles[len(rows)] = 1

        notes = "; ".join(variant.get("notes") or [])
        for semester in variant.get("balanced_semesters", []):
            if not semester.get("courses"):
                rows.append([semester.get("term", ""), semester.get("credits", ""), "", "", "", notes, ""])
                row_styles[len(rows)] = term_band_style(semester.get("term", ""))
                continue
            for course in semester.get("courses", []):
                rows.append(
                    [
                        semester.get("term", ""),
                        semester.get("credits", ""),
                        course.get("code", ""),
                        course.get("title", ""),
                        course.get("credits", ""),
                        notes,
                        "",
                    ]
                )
                row_styles[len(rows)] = term_band_style(semester.get("term", ""))
        rows.append(["", "", "", "", "", "", ""])

    sheet = {
        "name": "Balanced Plans",
        "rows": rows,
        "widths": [18, 14, 22, 44, 12, 96, 12],
        "row_styles": row_styles,
    }
    return anchors, sheet


def picker_row(variant: dict[str, Any], anchors: dict[str, int], balanced_anchors: dict[str, int], group: str) -> list[Any]:
    target_cell = f"A{anchors[variant['id']]}"
    balanced_target_cell = f"A{balanced_anchors[variant['id']]}"
    validation = variant.get("validation", {})
    return [
        link_to("Plans", target_cell, "Open plan"),
        link_to("Balanced Plans", balanced_target_cell, "Open balanced"),
        group,
        variant["id"],
        ", ".join(variant.get("cs_tracks") or []),
        ", ".join(variant.get("extras") or []) or "none",
        variant.get("target_graduation", ""),
        variant.get("max_semester_credits", ""),
        variant.get("planned_credits", ""),
        variant.get("placeholder_credits", ""),
        faster_21_label(variant),
        "OK"
        if all(
            validation.get(key)
            for key in [
                "target_tracks_ok",
                "math_ok",
                "statistics_ok",
                "finance_ok",
                "prerequisites_ok",
                "restriction_checks_ok",
            ]
        )
        else "CHECK",
        "; ".join(variant.get("notes") or []),
    ]


def start_here_sheet(data: dict[str, Any], anchors: dict[str, int], balanced_anchors: dict[str, int]) -> dict[str, Any]:
    variants = data.get("variants", [])
    by_id = {variant["id"]: variant for variant in variants}
    faster_plans = [plan for plan in selectable_plans(data) if plan.get("plan_kind") == "21-credit faster option"]
    all_plans = selectable_plans(data)

    rows: list[list[Any]] = [
        [
            "Open plan",
            "Open balanced plan",
            "Group",
            "Variant ID",
            "CS tracks",
            "Add-ons",
            "Graduation",
            "Max load",
            "Planned cr",
            "Placeholder cr",
            "21-credit option",
            "Validation",
            "Notes",
        ],
    ]
    row_styles: dict[int, int] = {}

    for variant_id in QUICK_PICK_IDS:
        variant = by_id.get(variant_id)
        if not variant:
            continue
        rows.append(picker_row(variant, anchors, balanced_anchors, "Quick pick"))
        row_styles[len(rows)] = 4 if variant.get("target_graduation") != "Fall 2028" else 5

    if faster_plans:
        rows.append(["", "", "", "", "", "", "", "", "", "", "", "", ""])
        rows.append(
            [
                "Open plan",
                "Open balanced plan",
                "Group",
                "Variant ID",
                "CS tracks",
                "Add-ons",
                "Graduation",
                "Max load",
                "Planned cr",
                "Placeholder cr",
                "21-credit option",
                "Validation",
                "Notes",
            ]
        )
        row_styles[len(rows)] = 1
        for plan in faster_plans:
            rows.append(picker_row(plan, anchors, balanced_anchors, "21-credit faster option"))
            row_styles[len(rows)] = 5

    rows.append(["", "", "", "", "", "", "", "", "", "", "", "", ""])
    rows.append(
        [
            "Open plan",
            "Open balanced plan",
            "Group",
            "Variant ID",
            "CS tracks",
            "Add-ons",
            "Graduation",
            "Max load",
            "Planned cr",
            "Placeholder cr",
            "21-credit option",
            "Validation",
            "Notes",
        ]
    )
    row_styles[len(rows)] = 1

    for plan in all_plans:
        rows.append(picker_row(plan, anchors, balanced_anchors, "All selectable plans"))
        row_styles[len(rows)] = 5 if plan.get("plan_kind") == "21-credit faster option" else (4 if plan.get("target_graduation") != "Fall 2028" else 5)

    return {
        "name": "Start Here",
        "rows": rows,
        "widths": [14, 20, 14, 54, 60, 60, 16, 10, 12, 14, 26, 12, 96],
        "row_styles": row_styles,
    }


def summary_sheet(data: dict[str, Any], anchors: dict[str, int]) -> dict[str, Any]:
    variants = data.get("variants", [])
    plans = selectable_plans(data)
    algorithms = next(variant for variant in variants if variant["id"] == "cs_algorithms")
    heavy = next(variant for variant in variants if variant["id"] == "cs_algorithms_machine_intelligence_cse_math_statistics_finance")
    heavy_21_id = f"{heavy['id']}__21_credit"
    heavy_link_id = heavy_21_id if heavy_21_id in anchors else heavy["id"]
    rows = [
        ["Item", "Value", "Jump"],
        ["Generated", data.get("generated_at", ""), ""],
        ["Base variants", len(variants), link_to("Start Here", "A1", "Open picker")],
        ["Selectable plans", len(plans), link_to("Start Here", "A1", "Open picker")],
        ["Preferred semester cap", data.get("preferred_semester_cap", ""), ""],
        ["Optional overload cap", data.get("overload_semester_cap", ""), ""],
        ["Completed listed credits", data.get("completed_credits_listed", ""), ""],
        [
            "CS Algorithms only fastest",
            f"{algorithms['target_graduation']} at max {fmt_credits(algorithms['max_semester_credits'])} credits",
            link_to("Plans", f"A{anchors[algorithms['id']]}", "Open plan"),
        ],
        [
            "Algorithms + Math/Stats/Finance fastest",
            "Spring 2028 under <=20 credits for Algorithms with Math, Statistics, Finance, and their combinations",
            link_to("Start Here", "A1", "Pick variant"),
        ],
        [
            "Heaviest combo fastest",
            f"{heavy['target_graduation']} at <=20; {heavy['overload_21']['target_graduation']} if allowing one {fmt_credits(heavy['overload_21']['max_semester_credits'])}-credit term",
            link_to("Plans", f"A{anchors[heavy_link_id]}", "Open fastest plan"),
        ],
        [
            "Summer note",
            "If CS 25200 is actually offered/approved in Summer 2027, CS Algorithms-only could move to Fall 2027. Summer availability is not verified here.",
            "",
        ],
    ]
    for assumption in data.get("assumptions", []):
        rows.append(["Assumption", assumption, ""])
    return {"name": "Summary", "rows": rows, "widths": [34, 120, 18]}


def how_to_use_sheet(data: dict[str, Any], anchors: dict[str, int]) -> dict[str, Any]:
    variants = data.get("variants", [])
    plans = selectable_plans(data)
    algorithms = next(variant for variant in variants if variant["id"] == "cs_algorithms")
    heavy_21 = "cs_algorithms_machine_intelligence_cse_math_statistics_finance__21_credit"
    rows = [
        ["Section", "What it is", "How to use it", "Jump"],
        [
            "Recommended workflow",
            "Start with the picker, then open one exact schedule.",
            "Go to Start Here, scan the quick picks or full list, then click Open plan on the row you care about.",
            link_to("Start Here", "A1", "Open picker"),
        ],
        [
            "Start Here",
            "The home page for choosing a plan.",
            "Use the Quick pick section for common plans. Use the 21-credit section for accelerated overload plans. Use the full list when you want every combination.",
            link_to("Start Here", "A1", "Open Start Here"),
        ],
        [
            "Plans",
            "The actual semester-by-semester schedules.",
            "Rows are grouped by plan. Each plan starts with its ID, target graduation, max load, and a Back to picker link.",
            link_to("Plans", f"A{anchors[algorithms['id']]}", "Open CS Algorithms"),
        ],
        [
            "Balanced Plans",
            "The same selectable plans with courses spread more evenly across the same target semester window.",
            "Use this when you prefer smoother semester loads over the front-loaded minimum-timeline layout.",
            link_to("Balanced Plans", "A1", "Open Balanced Plans"),
        ],
        [
            "Summary",
            "A short dashboard of the big conclusions.",
            "Use this when you just want the fastest answer and the main caveats without reading every row.",
            link_to("Summary", "A1", "Open Summary"),
        ],
        [
            "Course Lookup",
            "A flattened course list across every selectable plan.",
            "Filter by Course, Variant ID, Target, or Add-ons to answer questions like which plans include FIN 41650 or STAT 51200.",
            link_to("Course Lookup", "A1", "Open Course Lookup"),
        ],
        [
            "21 Credit Options",
            "The accelerated schedules that beat the <=20-credit version.",
            "Use this only for overload comparisons. These plans are also selectable directly from Start Here.",
            link_to("21 Credit Options", "A1", "Open 21-credit options"),
        ],
        [
            "Validation",
            "Local audit pass/fail checks for each selectable plan.",
            "Use this to confirm prerequisites, restrictions, and chosen add-ons pass the encoded local requirement model.",
            link_to("Validation", "A1", "Open Validation"),
        ],
        [
            "Colors",
            "Term bands in the schedule sheets.",
            "Schedule rows alternate by term, so all courses in the same semester share the same band color. Start Here and Validation still use green/yellow/red for status.",
            "",
        ],
        [
            "Plan IDs",
            "The ID names are long but systematic.",
            "`cs_algorithms_math_finance` means CS Algorithms plus Math BS plus Finance minor. IDs ending in `__21_credit` are separate accelerated overload plans.",
            link_to("Start Here", "A1", "Pick by ID"),
        ],
        [
            "Current scale",
            f"{len(variants)} base variants and {len(plans)} selectable plans.",
            "Selectable plans include the standard <=20-credit variants plus the faster 21-credit variants promoted as separate choices.",
            "",
        ],
    ]
    if heavy_21 in anchors:
        rows.append(
            [
                "Heaviest accelerated plan",
                "All CS tracks + Math + Statistics + Finance with one 21-credit term.",
                "This is the main case where allowing 21 credits changes the target from Fall 2028 to Spring 2028.",
                link_to("Plans", f"A{anchors[heavy_21]}", "Open accelerated plan"),
            ]
        )
    rows.extend(
        [
            ["Caveat", "Official advising still matters.", "The workbook uses local encoded requirements and scraped catalog prerequisites. It does not prove future course offerings, advisor approval, permits, or exact College of Science core category matching.", ""],
            ["Caveat", "Summer is not modeled as guaranteed.", "If CS 25200 is offered and approved in Summer 2027, the CS Algorithms-only plan could potentially move earlier, but this workbook keeps fall/spring plans separate from unverified summer assumptions.", ""],
        ]
    )
    return {"name": "How To Use", "rows": rows, "widths": [28, 42, 110, 24]}


def all_schedules_sheet(data: dict[str, Any], anchors: dict[str, int]) -> dict[str, Any]:
    rows = [
        [
            "Open",
            "Variant ID",
            "Target",
            "Max load",
            "CS tracks",
            "Add-ons",
            "Term",
            "Term credits",
            "Course",
            "Course name",
            "Credits",
        ]
    ]
    row_styles: dict[int, int] = {}
    for variant in selectable_plans(data):
        plan_link = link_to("Plans", f"A{anchors[variant['id']]}", "Open plan")
        for semester in variant.get("semesters", []):
            for course in semester.get("courses", []):
                rows.append(
                    [
                        plan_link,
                        variant["id"],
                        variant.get("target_graduation", ""),
                        variant.get("max_semester_credits", ""),
                        ", ".join(variant.get("cs_tracks") or []),
                        ", ".join(variant.get("extras") or []) or "none",
                        semester.get("term", ""),
                        semester.get("credits", ""),
                        course.get("code", ""),
                        course.get("title", ""),
                        course.get("credits", ""),
                    ]
                )
                row_styles[len(rows)] = term_band_style(semester.get("term", ""))
    return {
        "name": "Course Lookup",
        "rows": rows,
        "widths": [14, 54, 18, 10, 58, 58, 16, 12, 18, 42, 10],
        "row_styles": row_styles,
    }


def overload_sheet(data: dict[str, Any], anchors: dict[str, int]) -> dict[str, Any]:
    rows = [
        [
            "Open 21-credit plan",
            "Open <=20 plan",
            "Variant ID",
            "<=20 target",
            "<=20 max load",
            "<=21 target",
            "<=21 max load",
            "Term",
            "Term credits",
            "Course",
            "Course name",
            "Credits",
        ]
    ]
    row_styles: dict[int, int] = {}
    for variant in selectable_plans(data):
        overload = variant.get("overload_21") or {}
        if not overload or overload.get("target_graduation") == variant.get("target_graduation"):
            continue
        base_link = link_to("Plans", f"A{anchors[variant['id']]}", "Open <=20")
        overload_id = f"{variant['id']}__21_credit"
        overload_link = link_to("Plans", f"A{anchors[overload_id]}", "Open 21")
        for semester in overload.get("semesters", []):
            for course in semester.get("courses", []):
                rows.append(
                    [
                        overload_link,
                        base_link,
                        variant["id"],
                        variant.get("target_graduation", ""),
                        variant.get("max_semester_credits", ""),
                        overload.get("target_graduation", ""),
                        overload.get("max_semester_credits", ""),
                        semester.get("term", ""),
                        semester.get("credits", ""),
                        course.get("code", ""),
                        course.get("title", ""),
                        course.get("credits", ""),
                    ]
                )
                row_styles[len(rows)] = term_band_style(semester.get("term", ""))
    if len(rows) == 1:
        rows.append(["No <=21 plan is faster than the <=20 plan except where listed in Start Here.", "", "", "", "", "", "", "", "", "", "", ""])
    return {
        "name": "21 Credit Options",
        "rows": rows,
        "widths": [20, 18, 54, 18, 14, 18, 14, 16, 12, 18, 42, 10],
        "row_styles": row_styles,
    }


def validation_sheet(data: dict[str, Any], anchors: dict[str, int]) -> dict[str, Any]:
    rows = [["Open", "Variant ID", "Check", "Status", "Warnings"]]
    row_styles: dict[int, int] = {}
    for variant in selectable_plans(data):
        validation = variant.get("validation", {})
        checks = [
            ("Target CS tracks", validation.get("target_tracks_ok")),
            ("Math add-on", validation.get("math_ok")),
            ("Statistics add-on", validation.get("statistics_ok")),
            ("Finance add-on", validation.get("finance_ok")),
            ("Prerequisites", validation.get("prerequisites_ok")),
            ("Restriction checks", validation.get("restriction_checks_ok")),
        ]
        plan_link = link_to("Plans", f"A{anchors[variant['id']]}", "Open plan")
        for label, ok in checks:
            rows.append([plan_link, variant["id"], label, "OK" if ok else "CHECK", "; ".join(validation.get("warnings") or [])])
            row_styles[len(rows)] = 4 if ok else 6
    return {"name": "Validation", "rows": rows, "widths": [14, 54, 24, 12, 100], "row_styles": row_styles}


def make_workbook(output: Path, data_dir: Path = ROOT) -> None:
    # plan_variants.yaml is a personal, generated artifact and lives in data_dir.
    variants_path = data_dir / "plan_variants.yaml"
    if not variants_path.exists():
        raise FileNotFoundError(f"plan_variants.yaml not found in {data_dir}. Run generate_plan_variants.py first.")
    data = load_yaml(variants_path)
    anchors, plans = build_plan_rows(data)
    balanced_anchors, balanced_plans = build_balanced_plan_rows(data)
    sheets = [
        how_to_use_sheet(data, anchors),
        start_here_sheet(data, anchors, balanced_anchors),
        plans,
        balanced_plans,
        summary_sheet(data, anchors),
        all_schedules_sheet(data, anchors),
        overload_sheet(data, anchors),
        validation_sheet(data, anchors),
    ]
    write_xlsx(output, sheets)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Directory holding plan_variants.yaml and where plan_variants.xlsx is written. "
            "Defaults to the script directory. Point at a plan folder kept outside this repo, "
            "e.g. ../my-plan"
        ),
    )
    parser.add_argument("--output", default="plan_variants.xlsx")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else ROOT
    output = Path(args.output)
    if not output.is_absolute():
        output = data_dir / output
    make_workbook(output, data_dir)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
