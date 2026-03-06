#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extract worksheet and dashboard metadata from Tableau .twb XML.

Provides structured data classes that capture:
- Worksheet mark types, shelves (rows/cols), encodings, filters
- Dashboard composition (which worksheets are included)
- Field references with datasource context
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from lxml import etree


@dataclass
class FieldRef:
    """A reference to a field on a shelf or encoding."""
    raw: str  # Original bracket expression e.g. "[ds].[field]" or "[field]"
    datasource: Optional[str] = None
    column: Optional[str] = None
    role: Optional[str] = None  # "dimension" or "measure"
    derivation: Optional[str] = None  # e.g. "sum", "attr", "none", "year"

    @staticmethod
    def parse(text: str) -> "FieldRef":
        """Parse a Tableau field reference like '[ds].[col]' or 'SUM([col])'."""
        text = text.strip()
        derivation = None

        # Extract aggregation/derivation prefix: SUM([...]), ATTR([...]), YEAR([...])
        m = re.match(r"^([A-Z]+)\((.+)\)$", text)
        if m:
            derivation = m.group(1).lower()
            text = m.group(2).strip()

        # Parse [ds].[col] or [col]
        parts = re.findall(r"\[([^\]]+)\]", text)
        if len(parts) >= 2:
            return FieldRef(raw=text, datasource=parts[0], column=parts[1], derivation=derivation)
        elif len(parts) == 1:
            return FieldRef(raw=text, column=parts[0], derivation=derivation)
        else:
            return FieldRef(raw=text, derivation=derivation)


@dataclass
class EncodingInfo:
    """Encoding channel assignment (Color, Size, Shape, Label, Detail, Tooltip)."""
    channel: str  # e.g. "color", "size", "shape", "label", "detail", "tooltip"
    fields: List[FieldRef] = field(default_factory=list)


@dataclass
class WorksheetFilter:
    """A filter applied at the worksheet level."""
    column: str  # e.g. "[ds].[field]"
    filter_class: Optional[str] = None  # e.g. "categorical", "quantitative", "relative-date"
    include_values: Optional[List[str]] = None
    exclude_values: Optional[List[str]] = None


@dataclass
class WorksheetInfo:
    """Full metadata for a single Tableau worksheet (sheet/viz)."""
    name: str
    mark_class: str = "Automatic"
    datasource_name: Optional[str] = None
    rows_fields: List[FieldRef] = field(default_factory=list)
    cols_fields: List[FieldRef] = field(default_factory=list)
    pages_fields: List[FieldRef] = field(default_factory=list)
    encodings: List[EncodingInfo] = field(default_factory=list)
    filters: List[WorksheetFilter] = field(default_factory=list)
    used_columns: Set[str] = field(default_factory=set)


@dataclass
class DashboardInfo:
    """Dashboard composition info."""
    name: str
    worksheets: List[str] = field(default_factory=list)  # worksheet names


def _parse_shelf_fields(shelf_text: Optional[str]) -> List[FieldRef]:
    """Parse fields from a rows/cols shelf attribute text."""
    if not shelf_text:
        return []
    # Fields are space-separated bracket expressions, possibly with aggregations
    # e.g. '[System].[Metric]' or 'SUM([Amount])' or '[ds].[col]:1'
    refs = []
    # Match: optional AGG( ... ) wrapping bracket expressions
    for m in re.finditer(r"(?:[A-Z]+\()?\[[^\]]+\](?:\.\[[^\]]+\])?(?::[^\s]*)?\)?", shelf_text):
        refs.append(FieldRef.parse(m.group(0)))
    return refs


def extract_worksheets(root: etree._Element) -> List[WorksheetInfo]:
    """Extract all worksheet metadata from a TWB root element."""
    worksheets = []
    for ws in root.findall(".//worksheet"):
        name = ws.get("name") or "Untitled"
        info = WorksheetInfo(name=name)

        # Mark class
        table = ws.find(".//table")
        if table is not None:
            # Pane-level mark
            mark = table.find(".//pane/mark")
            if mark is not None:
                info.mark_class = mark.get("class") or "Automatic"
            else:
                # Top-level mark
                mark = table.find(".//mark")
                if mark is not None:
                    info.mark_class = mark.get("class") or "Automatic"

        # Rows / Cols
        view = ws.find(".//table/view")
        if view is not None:
            rows_el = view.find("rows")
            cols_el = view.find("cols")
            if rows_el is not None and rows_el.text:
                info.rows_fields = _parse_shelf_fields(rows_el.text)
            if cols_el is not None and cols_el.text:
                info.cols_fields = _parse_shelf_fields(cols_el.text)

        # Pages shelf
        for page_el in ws.findall(".//table/panes/pane/pages/page"):
            if page_el.text:
                info.pages_fields.append(FieldRef.parse(page_el.text))

        # Encodings from pane/encodings
        encoding_map: Dict[str, List[FieldRef]] = {}
        for enc in ws.findall(".//table/pane/encodings/*"):
            channel = enc.tag  # e.g. "color", "size", "shape", "lod" (detail), "text" (label)
            col_attr = enc.get("column")
            if col_attr:
                channel_name = channel
                if channel == "lod":
                    channel_name = "detail"
                elif channel == "text":
                    channel_name = "label"
                encoding_map.setdefault(channel_name, []).append(FieldRef.parse(col_attr))

        # Also check encoding elements at panes level
        for enc in ws.findall(".//table/panes/pane/encodings/*"):
            channel = enc.tag
            col_attr = enc.get("column")
            if col_attr:
                channel_name = channel
                if channel == "lod":
                    channel_name = "detail"
                elif channel == "text":
                    channel_name = "label"
                encoding_map.setdefault(channel_name, []).append(FieldRef.parse(col_attr))

        for ch, fields in encoding_map.items():
            info.encodings.append(EncodingInfo(channel=ch, fields=fields))

        # Tooltip encoding from mark/encoding with type="tooltip"
        for tooltip_enc in ws.findall(".//mark/encoding[@attr='tooltip']"):
            col_attr = tooltip_enc.get("column")
            if col_attr:
                encoding_map.setdefault("tooltip", []).append(FieldRef.parse(col_attr))

        # Worksheet-level filters
        for filt in ws.findall(".//table/view/filter"):
            col = filt.get("column") or ""
            fclass = filt.get("class")
            wf = WorksheetFilter(column=col, filter_class=fclass)

            # Categorical include/exclude
            groupfilter = filt.find(".//groupfilter")
            if groupfilter is not None:
                func = groupfilter.get("function")
                if func == "member":
                    member_val = groupfilter.get("member")
                    if member_val:
                        wf.include_values = [member_val.strip('"').strip("'")]
                elif func == "union":
                    members = []
                    for gf in groupfilter.findall(".//groupfilter[@function='member']"):
                        mv = gf.get("member")
                        if mv:
                            members.append(mv.strip('"').strip("'"))
                    if members:
                        wf.include_values = members

            info.filters.append(wf)

        # Datasource dependencies -> used columns
        for dep in ws.findall(".//datasource-dependencies"):
            ds_name = dep.get("datasource")
            if ds_name and not info.datasource_name:
                info.datasource_name = ds_name
            for col_el in dep.findall(".//column"):
                col_name = col_el.get("name")
                if col_name:
                    info.used_columns.add(col_name.strip("[]"))

        worksheets.append(info)
    return worksheets


def extract_dashboards(root: etree._Element) -> List[DashboardInfo]:
    """Extract dashboard composition from a TWB root element."""
    dashboards = []
    for db in root.findall(".//dashboards/dashboard"):
        name = db.get("name") or "Untitled Dashboard"
        dinfo = DashboardInfo(name=name)

        # Zones contain worksheet references
        for zone in db.findall(".//zone"):
            ws_name = zone.get("name")
            zone_type = zone.get("type")
            # type is not always present; check if name matches a worksheet
            if ws_name and zone_type != "layout-basic":
                # Avoid duplicates
                if ws_name not in dinfo.worksheets:
                    dinfo.worksheets.append(ws_name)

        dashboards.append(dinfo)
    return dashboards
