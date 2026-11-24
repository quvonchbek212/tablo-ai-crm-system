"""Portable exports of persisted connected-source analysis and query evidence."""
import csv
import html
import io
import json
import math
import re


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _csv_cell(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = _text(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def _sections(analysis):
    """Use executed evidence once, including legacy primary/chart-only results."""
    candidates = [
        {**result, "title": result.get("title") or result.get("purpose") or "So'rov natijasi"}
        for result in (analysis.get("query_results") or [])
    ]
    if analysis.get("sql_query") or analysis.get("rows") or analysis.get("columns") or not candidates:
        candidates.append({**analysis, "title": "So'rov natijasi"})
    charts = analysis.get("charts") or ([analysis["chart"]] if analysis.get("chart") else [])
    candidates.extend({**chart, "rows": chart.get("data") or [],
                       "id": chart.get("query_id") or chart.get("result_id"),
                       "title": chart.get("title") or "Grafik ma'lumotlari"} for chart in charts)
    seen_ids, seen_data = set(), set()
    for result in candidates:
        rows = result.get("rows") or []
        columns = result.get("columns") or list(dict.fromkeys(key for record in rows for key in record))
        sql = result.get("sql_query") or ""
        result_id = result.get("id")
        signature = json.dumps([sql.strip(), columns, rows], sort_keys=True, ensure_ascii=False)
        if (result_id and result_id in seen_ids) or signature in seen_data:
            continue
        if result_id:
            seen_ids.add(result_id)
        seen_data.add(signature)
        yield {"id": result_id or "", "title": result["title"], "columns": columns,
               "rows": rows, "sql": sql, "is_truncated": bool(result.get("is_truncated")),
               "returned_count": result.get("returned_count", len(rows)),
               "execution_time_ms": result.get("execution_time_ms")}


def _report_context(report, sections):
    analysis = report["analysis"]
    source = report.get("source") or {}
    truncated = bool(analysis.get("is_truncated")) or any(section["is_truncated"] for section in sections)
    metadata = [
        ("Hisobot", report.get("title", "Hisobot")),
        ("Manba", source.get("name") or source.get("database_name") or "Ulangan baza"),
        ("Baza turi", source.get("dialect", "")),
        ("Tayyorlangan vaqt", report.get("created_at", "")),
        ("So'rov", report.get("prompt", "")),
        ("Ulanish identifikatori", source.get("connection_fingerprint", "")),
        ("Baza tuzilishi identifikatori", source.get("schema_fingerprint", "")),
        ("Qaytarilgan qatorlar", len(analysis.get("rows") or [])),
        ("Natija bo'limlari", len(sections)),
        ("Natija cheklangan", "Ha" if truncated else "Yo'q"),
    ]
    if analysis.get("agent_status"):
        metadata.append(("Tahlil holati", analysis["agent_status"]))
    filters = report.get("filters") or {}
    if filters.get("start_date") or filters.get("end_date"):
        metadata.append(("Davr", f"{filters.get('start_date') or ''} - {filters.get('end_date') or ''}"))
    notes = [analysis.get("summary_text"), analysis.get("explanation"), *(analysis.get("validation_warnings") or [])]
    notes = list(dict.fromkeys(str(note) for note in notes if note))
    if truncated:
        notes.append("Eksport faqat qaytarilgan qatorlarni o'z ichiga oladi; to'liq baza eksporti emas.")
    return metadata, notes


def _section_metadata(section):
    values = [("Natija ID", section["id"]), ("Eksportdagi qatorlar", len(section["rows"])),
              ("Natija cheklangan", "Ha" if section["is_truncated"] else "Yo'q")]
    if section["execution_time_ms"] is not None:
        values.append(("Hisoblash vaqti, ms", section["execution_time_ms"]))
    return values


def export_connected_report(report, fmt):
    if fmt not in {"csv", "html", "xlsx", "json"}:
        raise ValueError("Eksport formati csv, html, xlsx yoki json bo'lishi kerak.")
    if fmt == "json":
        return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), "application/json; charset=utf-8"
    sections = list(_sections(report["analysis"]))
    metadata, notes = _report_context(report, sections)
    if fmt == "xlsx":
        return _xlsx_report(metadata, notes, sections), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if fmt == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        for record in metadata:
            writer.writerow([_csv_cell(cell) for cell in record])
        for note in notes:
            writer.writerow(["Izoh", _csv_cell(note)])
        for section in sections:
            writer.writerow([])
            writer.writerow([_csv_cell(section["title"])])
            for label, value in _section_metadata(section):
                writer.writerow([label, _csv_cell(value)])
            writer.writerow([_csv_cell(column) for column in section["columns"]])
            for record in section["rows"]:
                writer.writerow([_csv_cell(record.get(column)) for column in section["columns"]])
            if section["sql"]:
                writer.writerow(["SQL", _csv_cell(section["sql"])])
        return "\ufeff" + output.getvalue(), "text/csv; charset=utf-8"

    esc = lambda value: html.escape(_text(value), quote=True)
    details = "".join(f"<dt>{esc(label)}</dt><dd>{esc(value)}</dd>" for label, value in metadata)
    note_html = "".join(f"<p class='note'>{esc(note)}</p>" for note in notes)
    tables = []
    for section in sections:
        columns, rows, sql = section["columns"], section["rows"], section["sql"]
        head = "".join(f"<th scope='col'>{esc(column)}</th>" for column in columns)
        body = "".join("<tr>" + "".join(f"<td>{esc(record.get(column))}</td>" for column in columns) + "</tr>" for record in rows)
        provenance = " | ".join(f"{esc(label)}: {esc(value)}" for label, value in _section_metadata(section) if value != "")
        query = f"<details><summary>Hisoblash so'rovi</summary><pre>{esc(sql)}</pre></details>" if sql else ""
        table = f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>" if columns else "<p>Jadval natijasi yo'q.</p>"
        tables.append(f"<section><h2>{esc(section['title'])}</h2><p>{provenance}</p>{table}{query}</section>")
    content = f"""<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(report.get('title', 'Hisobot'))}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:24px;color:#172b4d;background:#f8faff}}h1,h2{{color:#174ea6}}dl{{display:grid;grid-template-columns:minmax(130px,1fr) 3fr;gap:8px 20px}}dt{{font-weight:600}}dd{{margin:0;overflow-wrap:anywhere}}section{{margin:28px 0}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #d9e2f2;overflow-wrap:anywhere}}th{{background:#e9f0ff}}pre,.note{{white-space:pre-wrap;overflow-wrap:anywhere}}@media(max-width:600px){{dl{{display:block}}dd{{margin-bottom:14px}}}}@media print{{body{{background:white;margin:0}}}}</style></head>
<body><h1>{esc(report.get('title', 'Hisobot'))}</h1><dl>{details}</dl>{note_html}{''.join(tables)}</body></html>"""
    return content, "text/html; charset=utf-8"


def _sheet_name(title, used):
    base = re.sub(r"[\[\]:*?/\\\x00-\x1f]", " ", str(title)).strip().strip("'")[:31] or "Natija"
    candidate, counter = base, 2
    while candidate.casefold() in used:
        suffix = f" ({counter})"
        candidate = base[:31 - len(suffix)] + suffix
        counter += 1
    used.add(candidate.casefold())
    return candidate


def _xlsx_report(metadata, notes, sections):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    summary = book.active
    summary.title = "Hisobot"
    used_names = {summary.title.casefold()}
    header_fill = PatternFill("solid", fgColor="245BD9")
    header_font = Font(color="FFFFFF", bold=True)
    transformations = set()

    def write(cell, value):
        if value is None:
            return
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) >= 10 ** 15:
            value = str(value)
            transformations.add("15 xonadan uzun butun sonlar aniqlikni saqlash uchun matn sifatida yozildi.")
        if isinstance(value, float) and not math.isfinite(value):
            value = str(value)
        if not isinstance(value, (str, int, float, bool)):
            value = _text(value)
        if isinstance(value, str):
            sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda match: f"\\u{ord(match[0]):04x}", value)
            if sanitized != value:
                transformations.add("Excel qo'llamaydigan boshqaruv belgilar \\uXXXX shaklida saqlandi.")
            if len(sanitized) > 32767:
                raise ValueError("Excel katagidagi matn 32767 belgidan oshdi. To'liq natija uchun JSON yoki CSV eksportini tanlang.")
            cell.value = sanitized
            cell.data_type = "s"
        else:
            cell.value = value

    def row(sheet, index, values):
        for column, value in enumerate(values, 1):
            write(sheet.cell(index, column), value)

    def header(sheet, index, columns):
        row(sheet, index, columns)
        for cell in sheet[index]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.row_dimensions[index].height = 28

    header(summary, 1, ["Ko'rsatkich", "Qiymat"])
    for index, values in enumerate([*metadata, *(("Izoh", note) for note in notes)], 2):
        row(summary, index, values)
    summary.freeze_panes = "A2"
    summary.column_dimensions["A"].width = 32
    summary.column_dimensions["B"].width = 86
    for cells in summary.iter_rows(min_row=2):
        cells[1].alignment = Alignment(wrap_text=True, vertical="top")

    for section in sections:
        if len(section["rows"]) > 1048568 or len(section["columns"]) > 16384:
            raise ValueError("Natija Excel varag'i chegarasidan oshdi. JSON yoki CSV eksportini tanlang.")
        sheet = book.create_sheet(_sheet_name(section["title"], used_names))
        sheet.sheet_properties.tabColor = "245BD9"
        row(sheet, 1, [section["title"]])
        sheet.cell(1, 1).font = Font(bold=True, color="174EA6", size=14)
        for index, values in enumerate(_section_metadata(section), 2):
            row(sheet, index, values)
        row(sheet, 6, ["SQL", section["sql"]])
        sheet.cell(6, 2).alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[6].height = 48
        columns = section["columns"]
        if columns:
            header(sheet, 8, columns)
            for index, record in enumerate(section["rows"], 9):
                row(sheet, index, [record.get(column) for column in columns])
            sheet.freeze_panes = "A9"
            sheet.auto_filter.ref = f"A8:{get_column_letter(len(columns))}{8 + len(section['rows'])}"
            for index, column in enumerate(columns, 1):
                width = max([len(str(column)), *[len(_text(record.get(column)).split('\n')[0]) for record in section["rows"][:200]]])
                sheet.column_dimensions[get_column_letter(index)].width = min(60, max(14, width + 3))
        else:
            sheet.column_dimensions["A"].width = 32
            sheet.column_dimensions["B"].width = 86
    for note in sorted(transformations):
        row(summary, summary.max_row + 1, ["Excel izohi", note])
        summary.cell(summary.max_row, 2).alignment = Alignment(wrap_text=True)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
