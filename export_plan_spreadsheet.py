#!/usr/bin/env python3
"""Export the degree plan and audit into a formatted XLSX workbook.

This intentionally uses only the Python standard library for the XLSX container
so the export works without adding spreadsheet dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import yaml


ROOT = Path(__file__).resolve().parent


def normalize_code(value: str) -> str:
    value = " ".join(str(value).strip().upper().replace("-", " ").split())
    match = re.match(r"^([A-Z]+)\s*([0-9][0-9A-Z]{2,5})$", value)
    if match:
        subject, number = match.groups()
        if number.isdigit() and len(number) < 5:
            number = number.zfill(5)
        return f"{subject} {number}"
    return value


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def term_index(plan: dict[str, Any]) -> dict[str, int]:
    return {semester["term"]: idx for idx, semester in enumerate(plan.get("semesters", []), start=1)}


def build_usage(audit: dict[str, Any]) -> dict[str, list[str]]:
    usage: defaultdict[str, list[str]] = defaultdict(list)
    for section in ("cs", "math", "finance", "statistics_math_emphasis"):
        for code, labels in audit.get(section, {}).get("usage", {}).items():
            usage[normalize_code(code)].extend(labels)
    return {code: sorted(set(labels)) for code, labels in usage.items()}


def split_usage(labels: list[str]) -> dict[str, str]:
    buckets = {"CS": [], "Math": [], "Finance": [], "Statistics": [], "Other": []}
    for label in labels:
        if label.startswith("CS ") or "Algorithmic" in label or "Machine Intelligence" in label or "Computational Science" in label:
            buckets["CS"].append(label)
        elif label.startswith("Math"):
            buckets["Math"].append(label)
        elif label.startswith("Finance"):
            buckets["Finance"].append(label)
        elif label.startswith("Statistics"):
            buckets["Statistics"].append(label)
        else:
            buckets["Other"].append(label)
    return {key: "; ".join(value) for key, value in buckets.items()}


def strip_atom_label(label: str) -> str:
    label = label.replace(" (concurrent allowed)", "")
    return label if re.match(r"^[A-Z]+ [A-Z]?[0-9]{3,5}$", label) else ""


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


def completed_courses(completed: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for course in completed.get("courses", []):
        row = dict(course)
        row["code"] = normalize_code(row["code"])
        rows.append(row)
    return rows


def check_map(audit: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {normalize_code(row["code"]): row for row in audit.get(key, [])}


def build_edges(audit: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    for check in audit.get("prerequisite_checks", []):
        to_code = normalize_code(check["code"])
        for label in check.get("satisfied_by", []):
            from_code = strip_atom_label(label)
            if not from_code:
                continue
            edges.append(
                {
                    "from": from_code,
                    "to": to_code,
                    "to_term": check["term"],
                    "relationship": "prerequisite",
                    "note": "Concurrent allowed" if "concurrent allowed" in label else "",
                }
            )
    return edges


def row_style_for_code(code: str, is_blocked: bool = False) -> int:
    if is_blocked:
        return 6
    if code.startswith("CS "):
        return 7
    if code.startswith("MA "):
        return 8
    if code.startswith("STAT "):
        return 9
    if code.startswith("FIN "):
        return 10
    if code.startswith("PLACEHOLDER"):
        return 11
    return 3


def col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def cell_xml(row: int, col: int, value: Any, style: int = 0) -> str:
    ref = f"{col_name(col)}{row}"
    style_attr = f' s="{style}"' if style else ""
    if value is None:
        value = ""
    if isinstance(value, dict) and "formula" in value:
        formula = escape(str(value["formula"]))
        cached = value.get("cached")
        if isinstance(cached, str):
            return f'<c r="{ref}" t="str"{style_attr}><f>{formula}</f><v>{escape(cached)}</v></c>'
        if isinstance(cached, (int, float)) and not isinstance(cached, bool):
            return f'<c r="{ref}"{style_attr}><f>{formula}</f><v>{cached}</v></c>'
        return f'<c r="{ref}"{style_attr}><f>{formula}</f></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = escape(str(value))
    preserve = ' xml:space="preserve"' if text.strip() != text or "\n" in text else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t{preserve}>{text}</t></is></c>'


def sheet_xml(name: str, rows: list[list[Any]], widths: list[float] | None = None, row_styles: dict[int, int] | None = None) -> str:
    row_styles = row_styles or {}
    widths = widths or []
    cols = ""
    if widths:
        parts = []
        for idx, width in enumerate(widths, start=1):
            parts.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
        cols = f"<cols>{''.join(parts)}</cols>"

    sheet_rows = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row, start=1):
            style = 1 if r_idx == 1 else row_styles.get(r_idx, 3)
            cells.append(cell_xml(r_idx, c_idx, value, style))
        height = ' ht="64" customHeight="1"' if name == "Flow Layout" and r_idx > 1 else ""
        sheet_rows.append(f'<row r="{r_idx}"{height}>{"".join(cells)}</row>')

    auto_filter = ""
    if rows and name != "Flow Layout":
        end_col = col_name(max(len(row) for row in rows))
        auto_filter = f'<autoFilter ref="A1:{end_col}{len(rows)}"/>'

    freeze = (
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{freeze}{cols}<sheetData>{''.join(sheet_rows)}</sheetData>{auto_filter}</worksheet>"
    )


def workbook_xml(sheet_names: list[str]) -> str:
    sheets = []
    for idx, name in enumerate(sheet_names, start=1):
        sheets.append(f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(sheets)}</sheets><calcPr calcMode=\"auto\" fullCalcOnLoad=\"1\"/></workbook>"
    )


def workbook_rels_xml(sheet_names: list[str]) -> str:
    rels = []
    for idx in range(1, len(sheet_names) + 1):
        rels.append(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
        )
    rels.append(
        f'<Relationship Id="rId{len(sheet_names) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(rels)}</Relationships>"
    )


def root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )


def content_types_xml(sheet_count: int) -> str:
    overrides = [
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for idx in range(1, sheet_count + 1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{''.join(overrides)}</Types>"
    )


def styles_xml() -> str:
    fills = [
        "FFFFFF",  # 0
        "1F4E78",  # 1 header
        "D9EAF7",  # 2
        "FFFFFF",  # 3 normal
        "E2F0D9",  # 4 ok
        "FFF2CC",  # 5 warning
        "F4CCCC",  # 6 blocked
        "DDEBF7",  # 7 CS
        "EADCF8",  # 8 MA
        "D9EAD3",  # 9 STAT
        "FCE4D6",  # 10 FIN
        "E7E6E6",  # 11 placeholder
    ]
    fill_xml = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>']
    for color in fills[1:]:
        fill_xml.append(f'<fill><patternFill patternType="solid"><fgColor rgb="FF{color}"/><bgColor indexed="64"/></patternFill></fill>')
    fonts = (
        '<fonts count="2">'
        '<font><sz val="11"/><color rgb="FF000000"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '</fonts>'
    )
    border = '<border><left/><right/><top/><bottom/><diagonal/></border>'
    borders = f'<borders count="1">{border}</borders>'
    cell_xfs = [
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>',
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>',
    ]
    for fill_id in range(3, 13):
        cell_xfs.append(
            f'<xf numFmtId="0" fontId="0" fillId="{fill_id}" borderId="0" xfId="0" applyFill="1" applyAlignment="1">'
            '<alignment wrapText="1" vertical="top"/></xf>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{fonts}<fills count=\"{len(fill_xml)}\">{''.join(fill_xml)}</fills>{borders}"
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f"<cellXfs count=\"{len(cell_xfs)}\">{''.join(cell_xfs)}</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )


def write_xlsx(path: Path, sheets: list[dict[str, Any]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml(len(sheets)))
        archive.writestr("_rels/.rels", root_rels_xml())
        archive.writestr("xl/workbook.xml", workbook_xml([sheet["name"] for sheet in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml([sheet["name"] for sheet in sheets]))
        archive.writestr("xl/styles.xml", styles_xml())
        for idx, sheet in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{idx}.xml",
                sheet_xml(sheet["name"], sheet["rows"], sheet.get("widths"), sheet.get("row_styles")),
            )


def make_workbook(output: Path) -> None:
    plan = load_yaml(ROOT / "plan.yaml")
    audit = load_json(ROOT / "audit_report.json")
    catalog = load_json(ROOT / "course_catalog.json").get("courses", {})
    completed = load_json(ROOT / "completed_courses.json")
    usage = build_usage(audit)
    prereq_checks = check_map(audit, "prerequisite_checks")
    restriction_checks = check_map(audit, "restriction_checks")
    edges = build_edges(audit)
    unlocks: defaultdict[str, list[str]] = defaultdict(list)
    for edge in edges:
        unlocks[edge["from"]].append(edge["to"])

    planned = planned_courses(plan)
    completed_rows = completed_courses(completed)
    terms = [semester["term"] for semester in plan.get("semesters", [])]

    semester_rows = [[
        "Term",
        "Course",
        "Course name",
        "Credits",
        "Degree elements satisfied",
        "CS elements",
        "Math BS elements",
        "Finance elements",
        "Statistics elements",
        "Prerequisites satisfied by",
        "Unlocks planned courses",
        "Prereq status",
        "Restriction status",
        "Notes",
    ]]
    semester_styles = {}
    for course in planned:
        code = course["code"]
        labels = usage.get(code, [])
        split = split_usage(labels)
        prereq = prereq_checks.get(code, {})
        restriction = restriction_checks.get(code, {})
        notes = []
        if course.get("original_code"):
            notes.append(f"Original code: {course['original_code']}")
        if course.get("replaces_blocked_preference"):
            notes.append(f"Replaces blocked preference: {course['replaces_blocked_preference']}")
        notes.extend(course.get("notes", []))
        restriction_status = "OK"
        if restriction.get("override_planned") and restriction.get("override_required"):
            restriction_status = "OVERRIDE PLANNED"
        elif not restriction.get("ok"):
            restriction_status = restriction.get("status", "")
        row = [
            course["term"],
            code,
            course.get("title", catalog.get(code, {}).get("title", "")),
            course.get("credits", ""),
            "; ".join(labels) if labels else "Other / elective / placeholder",
            split["CS"],
            split["Math"],
            split["Finance"],
            split["Statistics"],
            ", ".join(prereq.get("satisfied_by", [])) if prereq else "",
            ", ".join(sorted(set(unlocks.get(code, [])))),
            "OK" if prereq.get("ok") else prereq.get("status", ""),
            restriction_status,
            "\n".join(notes),
        ]
        semester_rows.append(row)
        blocked = not prereq.get("ok", True) or not restriction.get("ok", True)
        semester_styles[len(semester_rows)] = row_style_for_code(code, blocked)

    flow_columns = ["Completed / Transfer"] + terms
    flow_by_col: dict[str, list[str]] = {column: [] for column in flow_columns}
    for course in completed_rows:
        code = course["code"]
        if code.startswith("UND "):
            continue
        labels = usage.get(code, [])
        if labels or code in unlocks:
            flow_by_col["Completed / Transfer"].append(
                f"{code}\n{course.get('title', '')}\nReq: {'; '.join(labels) if labels else 'prerequisite credit'}\nUnlocks: {', '.join(sorted(set(unlocks.get(code, []))))}"
            )
    for course in planned:
        code = course["code"]
        prereq = prereq_checks.get(code, {})
        labels = usage.get(code, [])
        flow_by_col[course["term"]].append(
            f"{code}\n{course.get('title', '')}\nReq: {'; '.join(labels) if labels else 'other/elective'}\nFrom: {', '.join(prereq.get('satisfied_by', [])) or 'none'}\nUnlocks: {', '.join(sorted(set(unlocks.get(code, [])))) or 'none'}"
        )
    max_flow_rows = max(len(values) for values in flow_by_col.values())
    flow_rows = [flow_columns]
    for idx in range(max_flow_rows):
        flow_rows.append([flow_by_col[column][idx] if idx < len(flow_by_col[column]) else "" for column in flow_columns])
    flow_styles = {idx: 3 for idx in range(2, len(flow_rows) + 1)}

    edge_rows = [["Prerequisite course", "Prerequisite source term", "Unlocks course", "Unlocks term", "Relationship", "Note"]]
    code_to_term = {course["code"]: course["term"] for course in planned}
    for course in completed_rows:
        code_to_term.setdefault(course["code"], course.get("term", "Completed"))
    for edge in sorted(edges, key=lambda item: (item["to_term"], item["to"], item["from"])):
        edge_rows.append([
            edge["from"],
            code_to_term.get(edge["from"], "Completed / Transfer"),
            edge["to"],
            edge["to_term"],
            edge["relationship"],
            edge["note"],
        ])

    prereq_rows = [["Term", "Course", "Course name", "Status", "Satisfied by", "Best missing alternative", "Expression"]]
    prereq_styles = {}
    for check in audit.get("prerequisite_checks", []):
        prereq_rows.append([
            check["term"],
            check["code"],
            check.get("title", ""),
            "OK" if check.get("ok") else "BLOCKED",
            ", ".join(check.get("satisfied_by", [])),
            ", ".join(check.get("missing_best_alternative", [])),
            check.get("expression", ""),
        ])
        prereq_styles[len(prereq_rows)] = 4 if check.get("ok") else 6

    restriction_rows = [["Term", "Course", "Course name", "Status", "Restriction result", "Credits before term", "Raw restriction text"]]
    restriction_styles = {}
    for check in audit.get("restriction_checks", []):
        details = []
        for block in check.get("blocks", []):
            if block.get("ok"):
                if block.get("matched"):
                    details.append(f"{block.get('category')} matched: {', '.join(block['matched'])}")
                else:
                    details.append(f"{block.get('category')} exclusion clear")
            else:
                details.append(block.get("reason", "restricted"))
        if check.get("override_planned") and check.get("override_required"):
            details.insert(0, "Override/permit planned")
        restriction_rows.append([
            check["term"],
            check["code"],
            check.get("title", ""),
            "OVERRIDE PLANNED" if check.get("override_planned") and check.get("override_required") else ("OK" if check.get("ok") else "BLOCKED"),
            "; ".join(details) if details else "No restrictions",
            check.get("credits_before_term", ""),
            check.get("raw_restrictions_text", ""),
        ])
        restriction_styles[len(restriction_rows)] = 5 if check.get("override_planned") and check.get("override_required") else (4 if check.get("ok") else 6)

    coverage_rows = [["Course", "Course name", "Source/term", "Credits", "CS", "Math", "Finance", "Statistics", "All degree elements"]]
    all_course_rows = []
    for course in completed_rows:
        all_course_rows.append((course["code"], course.get("title", ""), course.get("term", "Completed"), course.get("credits", "")))
    for course in planned:
        all_course_rows.append((course["code"], course.get("title", ""), course["term"], course.get("credits", "")))
    seen = set()
    for code, title, source, credits in all_course_rows:
        if (code, source) in seen:
            continue
        seen.add((code, source))
        labels = usage.get(code, [])
        if not labels and not code.startswith("PLACEHOLDER"):
            continue
        split = split_usage(labels)
        coverage_rows.append([
            code,
            title,
            source,
            credits,
            split["CS"],
            split["Math"],
            split["Finance"],
            split["Statistics"],
            "; ".join(labels) if labels else "Other / placeholder",
        ])

    summary_rows = [
        ["Metric", "Value"],
        ["Target graduation", audit.get("target_graduation", "")],
        ["Completed listed credits", audit.get("credits", {}).get("completed_listed", "")],
        ["Planned credits", audit.get("credits", {}).get("planned", "")],
        ["Total if all listed credits count", audit.get("credits", {}).get("total_if_all_count", "")],
        ["Placeholder credits", audit.get("credits", {}).get("placeholder_credits", "")],
        ["Prerequisites OK", audit.get("prerequisites_ok", "")],
        ["Restrictions OK", audit.get("restrictions_ok", "")],
        ["Original CS/Math/Finance feasible", audit.get("base_feasible_by_fall_2028", "")],
        ["With Statistics Math Emphasis feasible", audit.get("feasible_by_fall_2028", "")],
    ]
    for load in audit.get("loads", []):
        summary_rows.append([f"{load['term']} credits", load["credits"]])

    sheets = [
        {
            "name": "Summary",
            "rows": summary_rows,
            "widths": [38, 28],
        },
        {
            "name": "Semester Plan",
            "rows": semester_rows,
            "widths": [14, 16, 34, 9, 44, 34, 28, 26, 34, 42, 34, 16, 18, 42],
            "row_styles": semester_styles,
        },
        {
            "name": "Flow Layout",
            "rows": flow_rows,
            "widths": [34, 34, 34, 34, 34, 34],
            "row_styles": flow_styles,
        },
        {
            "name": "Prereq Edges",
            "rows": edge_rows,
            "widths": [20, 22, 20, 16, 16, 24],
        },
        {
            "name": "Prereq Checks",
            "rows": prereq_rows,
            "widths": [14, 16, 34, 12, 46, 34, 80],
            "row_styles": prereq_styles,
        },
        {
            "name": "Restriction Checks",
            "rows": restriction_rows,
            "widths": [14, 16, 34, 12, 68, 18, 80],
            "row_styles": restriction_styles,
        },
        {
            "name": "Degree Coverage",
            "rows": coverage_rows,
            "widths": [16, 36, 22, 9, 36, 32, 30, 36, 56],
        },
    ]
    write_xlsx(output, sheets)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="graduation_plan.xlsx")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    make_workbook(output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
