#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generate Markdown reproduction guides from extracted Tableau worksheet/dashboard metadata.

Outputs:
- charts/index.md  - Overview with chart type mapping table, dashboard composition, sheet list
- charts/<sheet>.md - Per-sheet reproduction steps for Omni
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from twb_chart_extractor import (
    DashboardInfo,
    EncodingInfo,
    FieldRef,
    WorksheetFilter,
    WorksheetInfo,
)

# Tableau mark class -> Omni chart type mapping
CHART_TYPE_MAP: Dict[str, Dict[str, str]] = {
    "Automatic": {
        "omni_type": "Auto (Bar or Line)",
        "notes": "Omni auto-detects: temporal X-axis -> Line, categorical -> Bar",
    },
    "Bar": {
        "omni_type": "Bar chart",
        "notes": "Series Config で Grouped/Stacked/Stack% を選択",
    },
    "Line": {
        "omni_type": "Line chart",
        "notes": "X-axis に timestamp dimension を推奨",
    },
    "Area": {
        "omni_type": "Area chart",
        "notes": "Color facet で stacked area を実現",
    },
    "Text": {
        "omni_type": "Table (Pivot)",
        "notes": "Pivot 機能で crosstab 再現。pivot 上限200列",
    },
    "Circle": {
        "omni_type": "Scatterplot",
        "notes": "2 measures + optional size encoding",
    },
    "Square": {
        "omni_type": "Heatmap",
        "notes": "2 dimensions + 1 measure (color)",
    },
    "Pie": {
        "omni_type": "Pie/Donut chart",
        "notes": "inner radius 調整で Donut に変更可能",
    },
    "Map": {
        "omni_type": "Map (Point/Region)",
        "notes": "Point=lat/lon, Region=geo boundary. Tableau 程の高度な geo は未対応",
    },
    "Gantt Bar": {
        "omni_type": "Custom Vega-Lite",
        "notes": "x/x2 encoding で期間表現。ネイティブ非対応",
    },
    "Polygon": {
        "omni_type": "Custom Vega-Lite",
        "notes": "ネイティブ非対応",
    },
    "Shape": {
        "omni_type": "Scatterplot + shape series",
        "notes": "",
    },
    "Density": {
        "omni_type": "Custom Vega-Lite",
        "notes": "ネイティブ非対応",
    },
}


def _safe_filename(name: str) -> str:
    """Convert a worksheet name to a safe filename."""
    s = re.sub(r"[^\w\s\-]", "", name)
    s = re.sub(r"\s+", "_", s.strip())
    s = s.lower()
    return s or "sheet"


def _field_ref_display(ref: FieldRef, field_name_map: Optional[Dict[str, str]] = None) -> str:
    """Format a FieldRef for Markdown display."""
    _map = field_name_map or {}
    col = ref.column or ref.raw
    omni_key = _map.get(col, None)
    prefix = f"{ref.derivation.upper()}(" if ref.derivation else ""
    suffix = ")" if ref.derivation else ""
    if omni_key:
        return f"`{prefix}{omni_key}{suffix}` (Tableau: {col})"
    return f"`{prefix}{col}{suffix}`"


def _encoding_display(enc: EncodingInfo, field_name_map: Optional[Dict[str, str]] = None) -> str:
    """Format an encoding channel for Markdown."""
    fields_str = ", ".join(_field_ref_display(f, field_name_map) for f in enc.fields)
    return f"**{enc.channel.capitalize()}**: {fields_str}"


def _filter_display(filt: WorksheetFilter) -> str:
    """Format a worksheet filter for Markdown."""
    parts = [f"`{filt.column}`"]
    if filt.filter_class:
        parts.append(f"(type: {filt.filter_class})")
    if filt.include_values:
        vals = ", ".join(filt.include_values[:5])
        if len(filt.include_values) > 5:
            vals += f" ... (+{len(filt.include_values) - 5})"
        parts.append(f"include: [{vals}]")
    if filt.exclude_values:
        vals = ", ".join(filt.exclude_values[:5])
        parts.append(f"exclude: [{vals}]")
    return " ".join(parts)


def generate_sheet_markdown(
    ws: WorksheetInfo,
    field_name_map: Optional[Dict[str, str]] = None,
) -> str:
    """Generate per-sheet Markdown content."""
    chart_info = CHART_TYPE_MAP.get(ws.mark_class, {
        "omni_type": "Custom Vega-Lite (要確認)",
        "notes": f"Mark class '{ws.mark_class}' は Omni にネイティブ対応なし",
    })

    lines = [
        f"# {ws.name}",
        "",
        "## 1. Original Tableau Configuration",
        "",
        f"- **Mark Type**: `{ws.mark_class}`",
    ]

    if ws.datasource_name:
        lines.append(f"- **Datasource**: `{ws.datasource_name}`")

    # Rows / Cols
    if ws.cols_fields:
        cols_str = ", ".join(_field_ref_display(f, field_name_map) for f in ws.cols_fields)
        lines.append(f"- **Columns Shelf**: {cols_str}")
    if ws.rows_fields:
        rows_str = ", ".join(_field_ref_display(f, field_name_map) for f in ws.rows_fields)
        lines.append(f"- **Rows Shelf**: {rows_str}")

    # Pages
    if ws.pages_fields:
        pages_str = ", ".join(_field_ref_display(f, field_name_map) for f in ws.pages_fields)
        lines.append(f"- **Pages Shelf**: {pages_str}")

    # Encodings
    if ws.encodings:
        lines.append("")
        lines.append("### Encodings")
        for enc in ws.encodings:
            lines.append(f"- {_encoding_display(enc, field_name_map)}")

    # Filters
    if ws.filters:
        lines.append("")
        lines.append("### Worksheet Filters")
        for filt in ws.filters:
            lines.append(f"- {_filter_display(filt)}")

    # Used columns
    if ws.used_columns:
        lines.append("")
        lines.append("### Used Columns")
        for col in sorted(ws.used_columns):
            omni_key = (field_name_map or {}).get(col)
            if omni_key:
                lines.append(f"- `{col}` -> Omni: `{omni_key}`")
            else:
                lines.append(f"- `{col}`")

    # Omni reproduction steps
    lines.extend([
        "",
        "## 2. Omni Reproduction Steps",
        "",
        f"### Target Visualization: {chart_info['omni_type']}",
        "",
    ])

    if chart_info.get("notes"):
        lines.append(f"> {chart_info['notes']}")
        lines.append("")

    # Step-by-step
    lines.append("### Steps")
    lines.append("")

    step = 1
    # Topic selection
    lines.append(f"{step}. **Topic を選択**: 対応する Omni Topic を Workbook で開く")
    step += 1

    # Field selection
    field_instructions = []
    if ws.cols_fields:
        for f in ws.cols_fields:
            omni = (field_name_map or {}).get(f.column or "", None)
            display = omni or f.column or f.raw
            field_instructions.append(f"`{display}` を X-axis / Columns に配置")
    if ws.rows_fields:
        for f in ws.rows_fields:
            omni = (field_name_map or {}).get(f.column or "", None)
            display = omni or f.column or f.raw
            field_instructions.append(f"`{display}` を Y-axis / Rows に配置")

    if field_instructions:
        lines.append(f"{step}. **フィールドを配置**:")
        for fi in field_instructions:
            lines.append(f"   - {fi}")
        step += 1

    # Visualization type
    lines.append(f"{step}. **ビジュアライゼーション タイプ**: `{chart_info['omni_type']}` を選択")
    step += 1

    # Encoding setup
    enc_instructions = []
    for enc in ws.encodings:
        if enc.channel == "color":
            for f in enc.fields:
                omni = (field_name_map or {}).get(f.column or "", None)
                display = omni or f.column or f.raw
                enc_instructions.append(f"Color series に `{display}` を設定")
        elif enc.channel == "size":
            for f in enc.fields:
                omni = (field_name_map or {}).get(f.column or "", None)
                display = omni or f.column or f.raw
                enc_instructions.append(f"Size encoding に `{display}` を設定")

    if enc_instructions:
        lines.append(f"{step}. **エンコーディング設定**:")
        for ei in enc_instructions:
            lines.append(f"   - {ei}")
        step += 1

    # Filters
    if ws.filters:
        lines.append(f"{step}. **フィルター設定**: ダッシュボード フィルターまたは Topic default_filters で再現")
        step += 1

    # Limitations
    limitations = []
    if ws.mark_class in ("Gantt Bar", "Polygon", "Density"):
        limitations.append(f"`{ws.mark_class}` は Omni にネイティブ対応がないため Custom Vega-Lite が必要")
    if ws.pages_fields:
        limitations.append("Tableau Pages シェルフは Omni 非対応。ダッシュボード フィルターで代替")
    if ws.mark_class == "Text":
        limitations.append("Pivot 機能の上限は 200 列")

    if limitations:
        lines.append("")
        lines.append("### Limitations / Notes")
        for lim in limitations:
            lines.append(f"- {lim}")

    lines.append("")
    return "\n".join(lines)


def generate_index_markdown(
    worksheets: List[WorksheetInfo],
    dashboards: List[DashboardInfo],
) -> str:
    """Generate the charts/index.md overview."""
    lines = [
        "# Tableau -> Omni Chart Reproduction Guide",
        "",
        "## Chart Type Mapping",
        "",
        "| Tableau Mark | Omni Equivalent | Notes |",
        "|---|---|---|",
    ]

    # Collect unique mark types used
    used_marks = set(ws.mark_class for ws in worksheets)
    for mark, info in CHART_TYPE_MAP.items():
        used = " *" if mark in used_marks else ""
        lines.append(f"| {mark}{used} | {info['omni_type']} | {info.get('notes', '')} |")

    lines.extend([
        "",
        "> `*` = このワークブックで使用されている Mark Type",
        "",
    ])

    # Omni-specific viz types
    lines.extend([
        "### Omni 固有のビジュアライゼーション (Tableau に無いもの)",
        "",
        "KPI Cards, Funnel chart, Sankey chart, Boxplot, AI Summary, Markdown tile, Single record",
        "",
    ])

    # Dashboard composition
    if dashboards:
        lines.extend([
            "## Dashboards",
            "",
        ])
        for db in dashboards:
            lines.append(f"### {db.name}")
            lines.append("")
            if db.worksheets:
                lines.append("| # | Worksheet |")
                lines.append("|---|---|")
                for i, ws_name in enumerate(db.worksheets, 1):
                    safe = _safe_filename(ws_name)
                    lines.append(f"| {i} | [{ws_name}]({safe}.md) |")
            else:
                lines.append("(No worksheets detected)")
            lines.append("")

    # Sheet list
    lines.extend([
        "## Worksheets",
        "",
        "| # | Name | Mark Type | Omni Type | Cols | Rows |",
        "|---|---|---|---|---|---|",
    ])

    for i, ws in enumerate(worksheets, 1):
        chart_info = CHART_TYPE_MAP.get(ws.mark_class, {"omni_type": "Custom Vega-Lite"})
        safe = _safe_filename(ws.name)
        cols = len(ws.cols_fields)
        rows = len(ws.rows_fields)
        lines.append(f"| {i} | [{ws.name}]({safe}.md) | {ws.mark_class} | {chart_info['omni_type']} | {cols} | {rows} |")

    lines.append("")

    # Parameter / Field Switcher note
    lines.extend([
        "## Tableau Parameter -> Omni 代替",
        "",
        "- Tableau の「パラメータによるメトリクス切替」-> Omni の **Field Switcher** コントロールで対応",
        "- Tableau の「Pages シェルフ」-> Omni 非対応、ダッシュボード フィルターで代替",
        "",
    ])

    return "\n".join(lines)


def generate_chart_markdowns(
    worksheets: List[WorksheetInfo],
    dashboards: List[DashboardInfo],
    output_dir: str,
    field_name_map: Optional[Dict[str, str]] = None,
) -> None:
    """Generate all chart Markdown files to the output directory."""
    os.makedirs(output_dir, exist_ok=True)

    # index.md
    index_content = generate_index_markdown(worksheets, dashboards)
    with open(os.path.join(output_dir, "index.md"), "w", encoding="utf-8") as f:
        f.write(index_content)

    # Per-sheet markdowns
    for ws in worksheets:
        sheet_content = generate_sheet_markdown(ws, field_name_map)
        filename = _safe_filename(ws.name) + ".md"
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(sheet_content)
