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


# Derivations that indicate a measure (aggregation functions)
MEASURE_DERIVATIONS = frozenset({
    "sum", "avg", "average", "min", "max", "count", "countd",
    "median", "stdev", "var",
})

# Derivations that indicate a time grain (dimension with time truncation)
TIME_DERIVATIONS = frozenset({
    "year", "quarter", "month", "week", "day", "hour", "minute", "second",
    "datepart", "datetrunc",
})


def _classify_role(ref: FieldRef) -> str:
    """Classify a FieldRef as 'dimension' or 'measure'.

    Priority: explicit role > derivation-based heuristic > default 'dimension'.
    """
    if ref.role:
        return ref.role
    d = (ref.derivation or "").lower()
    if d in MEASURE_DERIVATIONS:
        return "measure"
    return "dimension"


def _derivation_annotation(ref: FieldRef) -> str:
    """Return a human-readable annotation for the derivation.

    Examples: '時間粒度: Year', '集計: SUM', '' (empty for none/attr/unknown).
    """
    d = (ref.derivation or "").lower()
    if not d or d in ("none", "attr"):
        return ""
    if d in TIME_DERIVATIONS:
        return f"時間粒度: {ref.derivation.capitalize()}"
    if d in MEASURE_DERIVATIONS:
        return f"集計: {ref.derivation.upper()}"
    return ""


def _collect_query_fields(
    ws: WorksheetInfo,
    field_name_map: Optional[Dict[str, str]] = None,
) -> tuple:
    """Collect all fields from cols/rows/encodings, deduplicate, and classify.

    Returns:
        (dimensions, measures) — each a list of (FieldRef, omni_display, annotation).
    """
    _map = field_name_map or {}
    seen: set = set()
    dimensions: list = []
    measures: list = []

    def _add(ref: FieldRef) -> None:
        key = (ref.column or ref.raw, ref.derivation)
        if key in seen:
            return
        seen.add(key)

        col = ref.column or ref.raw
        omni_name = _map.get(col, None)
        if omni_name:
            display = f"`{omni_name}` (Tableau: {col})"
        else:
            display = f"`{col}`"
        annotation = _derivation_annotation(ref)
        role = _classify_role(ref)

        entry = (ref, display, annotation)
        if role == "measure":
            measures.append(entry)
        else:
            dimensions.append(entry)

    for f in ws.cols_fields:
        _add(f)
    for f in ws.rows_fields:
        _add(f)
    for enc in ws.encodings:
        for f in enc.fields:
            _add(f)

    return dimensions, measures


def _parse_filter_column(column_expr: str) -> str:
    """Extract a plain field name from a bracket expression.

    '[ds].[field]' -> 'field', '[field]' -> 'field'.
    """
    parts = re.findall(r"\[([^\]]+)\]", column_expr)
    if parts:
        return parts[-1]
    return column_expr


def _safe_filename(name: str) -> str:
    """Convert a worksheet/dashboard name to a safe filename.

    Preserves Japanese characters (Python3 \\w matches Unicode word chars).
    Removes path-unsafe characters, replaces whitespace with underscore,
    and enforces a 100-character limit.
    """
    s = re.sub(r'[/\\:*?"<>|]', "", name)
    s = re.sub(r"\s+", "_", s.strip())
    s = s[:100]
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

    # 1. Topic selection — with datasource name
    ds_display = f"`{ws.datasource_name}`" if ws.datasource_name else "対応する Omni Topic"
    lines.append(f"{step}. **Topic を選択**: {ds_display} を Workbook で開く")
    step += 1

    # Collect and classify fields
    dimensions, measures = _collect_query_fields(ws, field_name_map)

    # 2. Dimension selection
    if dimensions:
        lines.append(f"{step}. **Dimension を選択** (Query Panel 左側の Field Picker):")
        for _ref, display, annotation in dimensions:
            suffix = f" — {annotation}" if annotation else ""
            lines.append(f"   - {display}{suffix}")
        step += 1

    # 3. Measure selection
    if measures:
        lines.append(f"{step}. **Measure を選択**:")
        for _ref, display, annotation in measures:
            suffix = f" — {annotation}" if annotation else ""
            lines.append(f"   - {display}{suffix}")
        step += 1

    # 4. Visualization type
    lines.append(f"{step}. **ビジュアライゼーション タイプ**: `{chart_info['omni_type']}` を選択")
    step += 1

    # 5. Encoding setup — all channels
    _channel_label_map = {
        "color": "Color series",
        "size": "Size encoding",
        "shape": "Shape encoding",
        "detail": "Detail (group by)",
        "label": "Label",
        "tooltip": "Tooltip",
    }
    enc_instructions = []
    for enc in ws.encodings:
        channel_label = _channel_label_map.get(enc.channel, enc.channel.capitalize())
        for f in enc.fields:
            omni = (field_name_map or {}).get(f.column or "", None)
            display = omni or f.column or f.raw
            enc_instructions.append(f"{channel_label} に `{display}` を設定")

    if enc_instructions:
        lines.append(f"{step}. **エンコーディング設定**:")
        for ei in enc_instructions:
            lines.append(f"   - {ei}")
        step += 1

    # 6. Filter setup — with Omni name mapping and details
    if ws.filters:
        lines.append(f"{step}. **フィルター設定**:")
        _map = field_name_map or {}
        for filt in ws.filters:
            col_name = _parse_filter_column(filt.column)
            omni_name = _map.get(col_name, None)
            if omni_name:
                filt_display = f"`{omni_name}` (Tableau: {col_name})"
            else:
                filt_display = f"`{col_name}`"
            detail_parts = []
            if filt.filter_class:
                detail_parts.append(f"type: {filt.filter_class}")
            if filt.include_values:
                vals = ", ".join(filt.include_values[:5])
                if len(filt.include_values) > 5:
                    vals += f" ... (+{len(filt.include_values) - 5})"
                detail_parts.append(f"include: [{vals}]")
            if filt.exclude_values:
                vals = ", ".join(filt.exclude_values[:5])
                if len(filt.exclude_values) > 5:
                    vals += f" ... (+{len(filt.exclude_values) - 5})"
                detail_parts.append(f"exclude: [{vals}]")
            detail_suffix = f" — {', '.join(detail_parts)}" if detail_parts else ""
            lines.append(f"   - {filt_display}{detail_suffix}")
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


def generate_dashboard_markdown(
    db: DashboardInfo,
    worksheets: List[WorksheetInfo],
    field_name_map: Optional[Dict[str, str]] = None,
    ws_filename_map: Optional[Dict[str, str]] = None,
) -> str:
    """Generate a per-dashboard Markdown file.

    Args:
        db: Dashboard metadata.
        worksheets: All worksheets in the workbook.
        field_name_map: Tableau column -> Omni field name map.
        ws_filename_map: Worksheet name -> output filename (without .md) map.
    """
    ws_map = {ws.name: ws for ws in worksheets}
    _fname_map = ws_filename_map or {}

    lines = [
        f"# Dashboard: {db.name}",
        "",
        "## 概要",
        f"このダッシュボードは {len(db.worksheets)} 個のワークシートで構成されています。",
        "",
        "## 構成ワークシート",
        "| # | Worksheet | Chart Type | Omni Type | Migration Guide |",
        "|---|-----------|------------|-----------|-----------------|",
    ]

    for i, ws_name in enumerate(db.worksheets, 1):
        ws = ws_map.get(ws_name)
        if ws:
            chart_info = CHART_TYPE_MAP.get(ws.mark_class, {"omni_type": "Custom Vega-Lite"})
            fname = _fname_map.get(ws_name, _safe_filename(ws_name))
            lines.append(
                f"| {i} | {ws_name} | {ws.mark_class} | {chart_info['omni_type']} | [{fname}.md]({fname}.md) |"
            )
        else:
            lines.append(f"| {i} | {ws_name} | - | - | - |")

    lines.extend([
        "",
        "## Omni ダッシュボード再現方針",
        "",
        "### レイアウト",
        "- Omni Dashboard で新規作成、各 WS を Tile として配置",
        "",
        "### 共有フィルター",
        "- Tableau ダッシュボードフィルター -> Omni Dashboard Filter Controls",
        "",
        "### 推奨再現手順",
        "1. 各 WS の Migration Guide に従い Workbook Query を作成",
        "2. Dashboard 新規作成 → Tile 配置",
        "3. Filter Controls 設定",
        "",
        "### Limitations",
        "- floating layout は Omni で再現不可（grid-based のみ）",
        "- Action（highlight, URL, filter）は部分対応",
        "",
    ])

    return "\n".join(lines)


def generate_index_markdown(
    worksheets: List[WorksheetInfo],
    dashboards: List[DashboardInfo],
    ws_filename_map: Optional[Dict[str, str]] = None,
    db_filename_map: Optional[Dict[str, str]] = None,
) -> str:
    """Generate the charts/index.md overview."""
    _ws_fmap = ws_filename_map or {}
    _db_fmap = db_filename_map or {}
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
            db_fname = _db_fmap.get(db.name)
            if db_fname:
                lines.append(f"### [{db.name}]({db_fname}.md)")
            else:
                lines.append(f"### {db.name}")
            lines.append("")
            if db.worksheets:
                lines.append("| # | Worksheet |")
                lines.append("|---|---|")
                for i, ws_name in enumerate(db.worksheets, 1):
                    fname = _ws_fmap.get(ws_name, _safe_filename(ws_name))
                    lines.append(f"| {i} | [{ws_name}]({fname}.md) |")
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
        fname = _ws_fmap.get(ws.name, _safe_filename(ws.name))
        cols = len(ws.cols_fields)
        rows = len(ws.rows_fields)
        lines.append(f"| {i} | [{ws.name}]({fname}.md) | {ws.mark_class} | {chart_info['omni_type']} | {cols} | {rows} |")

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


def _build_filename_maps(
    worksheets: List[WorksheetInfo],
    dashboards: List[DashboardInfo],
    domain_labels: Optional[Dict] = None,
) -> tuple:
    """Build ws_filename_map and db_filename_map from domain_labels.

    Returns:
        (ws_filename_map, db_filename_map) - name -> filename (without .md)
    """
    dl = domain_labels or {}
    ws_labels = dl.get("worksheets", {})
    db_labels = dl.get("dashboards", {})

    ws_filename_map: Dict[str, str] = {}
    for ws in worksheets:
        safe = _safe_filename(ws.name)
        label = ws_labels.get(ws.name, "")
        if label:
            ws_filename_map[ws.name] = f"worksheet_{safe}_{_safe_filename(label)}"
        else:
            ws_filename_map[ws.name] = f"worksheet_{safe}"

    db_filename_map: Dict[str, str] = {}
    for db in dashboards:
        safe = _safe_filename(db.name)
        label = db_labels.get(db.name, "")
        if label:
            db_filename_map[db.name] = f"dashboard_{safe}_{_safe_filename(label)}"
        else:
            db_filename_map[db.name] = f"dashboard_{safe}"

    return ws_filename_map, db_filename_map


def generate_chart_markdowns(
    worksheets: List[WorksheetInfo],
    dashboards: List[DashboardInfo],
    output_dir: str,
    field_name_map: Optional[Dict[str, str]] = None,
    domain_labels: Optional[Dict] = None,
    twb_filename: Optional[str] = None,
) -> None:
    """Generate all chart Markdown files to the output directory.

    Args:
        worksheets: Extracted worksheet metadata.
        dashboards: Extracted dashboard metadata.
        output_dir: Base output directory. If twb_filename is set and output_dir
            was not explicitly provided, output goes to
            ``migration-guides-twb2omni/<twb_filename>/``.
        field_name_map: Tableau column -> Omni field name map.
        domain_labels: AI-generated domain labels. Format:
            ``{"worksheets": {"Sheet1": "遅延率推移"}, "dashboards": {"DB1": "運航概況"}}``
        twb_filename: TWB filename (without extension). When set and output_dir
            is the default ``--charts-out`` value, overrides output_dir to
            ``migration-guides-twb2omni/<twb_filename>/``.
    """
    # Resolve output directory
    if twb_filename and not output_dir:
        output_dir = os.path.join("migration-guides-twb2omni", twb_filename)

    os.makedirs(output_dir, exist_ok=True)

    # Build filename maps
    ws_filename_map, db_filename_map = _build_filename_maps(
        worksheets, dashboards, domain_labels
    )

    # index.md
    index_content = generate_index_markdown(
        worksheets, dashboards, ws_filename_map, db_filename_map
    )
    with open(os.path.join(output_dir, "index.md"), "w", encoding="utf-8") as f:
        f.write(index_content)

    # Per-sheet markdowns
    for ws in worksheets:
        sheet_content = generate_sheet_markdown(ws, field_name_map)
        filename = ws_filename_map.get(ws.name, f"worksheet_{_safe_filename(ws.name)}") + ".md"
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(sheet_content)

    # Per-dashboard markdowns
    for db in dashboards:
        db_content = generate_dashboard_markdown(
            db, worksheets, field_name_map, ws_filename_map
        )
        filename = db_filename_map.get(db.name, f"dashboard_{_safe_filename(db.name)}") + ".md"
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(db_content)
