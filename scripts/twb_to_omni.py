#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tableau .twb (XML) -> Omni semantic layer YAML generator.

Key behaviors:
- Create views from table relations and custom SQL relations
- Create relationships.yml from join relations (best effort)
- Create ONE topic per Tableau datasource
- Convert Tableau LOD calcs to Omni level_of_detail where possible
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

import yaml
from lxml import etree


# ----------------------------
# Utilities
# ----------------------------

def snake(s: str) -> str:
    """Convert a string to a snake_case ASCII-only identifier."""
    s = (s or "").strip()
    s = re.sub(r"[\[\]\(\)\{\}]+", "", s)
    # Replace non-ASCII characters with underscore for English-only field names
    s = re.sub(r"[^\x00-\x7f]", "_", s)
    s = re.sub(r"[^\w]+", "_", s)
    s = s.lower().strip("_")
    # Collapse multiple underscores
    s = re.sub(r"_+", "_", s)
    if not s:
        s = "field"
    if not re.match(r"^[a-z]", s):
        s = "f_" + s
    return s


def make_field_key(
    caption: str,
    internal_name: Optional[str] = None,
    used_keys: Optional[Set[str]] = None,
) -> str:
    """Generate a unique ASCII field key from caption, with fallback to internal name."""
    _used = used_keys or set()

    key = snake(caption)

    # If too generic (pure-Japanese caption produced 'field'), try internal name
    if key in ("field", "f_") and internal_name:
        alt = snake(internal_name)
        if alt not in ("field", "f_") and len(alt) >= 2:
            key = alt

    # Deduplicate
    if key in _used:
        base = key
        counter = 2
        while f"{base}_{counter}" in _used:
            counter += 1
        key = f"{base}_{counter}"

    return key


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def write_yaml(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            obj,
            f,
            sort_keys=False,
            allow_unicode=True,
            width=120,
            default_flow_style=False,
        )


def text_or_none(node) -> Optional[str]:
    if node is None:
        return None
    t = node.text
    if t is None:
        return None
    t = t.strip("\n")
    t = t.strip()
    return t if t else None


# ----------------------------
# Omni models
# ----------------------------

@dataclass
class OmniView:
    name: str
    schema: Optional[str] = None
    table_name: Optional[str] = None
    sql: Optional[str] = None
    dimensions: Dict[str, dict] = field(default_factory=dict)
    measures: Dict[str, dict] = field(default_factory=dict)
    filters: Dict[str, dict] = field(default_factory=dict)

    def to_yaml_obj(self) -> dict:
        obj: Dict[str, object] = {"name": self.name}
        obj["schema"] = (self.schema or "PUBLIC")
        if self.sql:
            obj["sql"] = self.sql
        else:
            if self.table_name:
                obj["table_name"] = self.table_name
        if self.dimensions:
            obj["dimensions"] = self.dimensions
        if self.measures:
            obj["measures"] = self.measures
        if self.filters:
            obj["filters"] = self.filters
        return obj


@dataclass
class OmniRelationship:
    join_from_view: str
    join_to_view: str
    join_type: str = "always_left"
    on_sql: str = "/* TODO: fill join condition */ 1=1"
    relationship_type: str = "assumed_many_to_one"
    reversible: Optional[bool] = None

    def to_yaml_obj(self) -> dict:
        obj = {
            "join_from_view": self.join_from_view,
            "join_to_view": self.join_to_view,
            "join_type": self.join_type,
            "on_sql": self.on_sql,
            "relationship_type": self.relationship_type,
        }
        if self.reversible is not None:
            obj["reversible"] = self.reversible
        return obj


@dataclass
class OmniTopic:
    name: str
    label: Optional[str] = None
    group_label: Optional[str] = None
    base_view: Optional[str] = None
    joins: dict = field(default_factory=dict)
    always_where_sql: Optional[str] = None

    def to_yaml_obj(self) -> dict:
        obj: Dict[str, object] = {}
        if self.label:
            obj["label"] = self.label
        if self.group_label:
            obj["group_label"] = self.group_label
        if self.base_view:
            obj["base_view"] = self.base_view
        if self.joins:
            obj["joins"] = self.joins
        if self.always_where_sql:
            obj["always_where_sql"] = self.always_where_sql
        return obj


# ----------------------------
# Tableau parsing & conversion
# ----------------------------

AGG_FUNCS = {
    "sum": "sum",
    "avg": "average",
    "average": "average",
    "min": "min",
    "max": "max",
    "count": "count",
    "countd": "count_distinct",
}

LOD_GROUPING_MAP = {
    "fixed": "fixed",
    "include": "always_include",
    "exclude": "always_exclude",
}


def is_lod_formula(formula: str) -> bool:
    f = (formula or "").strip()
    if not f.startswith("{"):
        return False
    fl = f.lower()
    return ("fixed" in fl) or ("include" in fl) or ("exclude" in fl)


def is_aggregated_formula(formula: str) -> bool:
    f = (formula or "").lower()
    return any(re.search(rf"\b{fn}\s*\(", f) for fn in ["sum", "avg", "average", "min", "max", "count", "countd"])


def try_unwrap_simple_aggregate(formula: str):
    """単純な AGG([field]) パターンなら (omni_agg_type, inner_expr) を返す。複雑なら None。"""
    f = (formula or "").strip()
    m = re.match(r"^([a-zA-Z]+)\s*\(", f)
    if not m:
        return None
    func_name = m.group(1).lower()
    agg_type = AGG_FUNCS.get(func_name)
    if not agg_type:
        return None
    # 関数の開き括弧に対応する閉じ括弧が式の末尾かチェック
    open_pos = m.end() - 1  # '(' の位置
    close_pos = _find_matching_paren(f, open_pos)
    if close_pos == -1 or close_pos != len(f) - 1:
        return None
    # 内部式を取得
    inner = f[open_pos + 1 : close_pos].strip()
    # 内部式に集計関数が含まれていないかチェック
    if is_aggregated_formula(inner):
        return None
    return (agg_type, inner)


def _find_matching_paren(s: str, start: int) -> int:
    """start位置の'('に対応する')'の位置を返す。見つからなければ-1。"""
    if start >= len(s) or s[start] != "(":
        return -1
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_args(s: str) -> List[str]:
    """括弧のネストを考慮してカンマで引数を分割する。"""
    args: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    args.append("".join(current).strip())
    return args


def _replace_func_call(sql: str, func_name: str, replacer: Callable[[List[str]], str]) -> str:
    """func_name(args...)パターンを見つけてreplacerで置換。ネスト対応。大文字小文字無視。"""
    pattern = re.compile(r"\b" + func_name + r"\s*\(", re.IGNORECASE)
    result = sql
    while True:
        m = pattern.search(result)
        if not m:
            break
        # '(' の位置を特定
        paren_start = result.index("(", m.start())
        paren_end = _find_matching_paren(result, paren_start)
        if paren_end == -1:
            break
        inner = result[paren_start + 1:paren_end]
        args = _split_args(inner)
        replacement = replacer(args)
        result = result[:m.start()] + replacement + result[paren_end + 1:]
    return result


def convert_tableau_syntax_to_sql(sql: str) -> str:
    """Tableau固有関数・構文を標準SQLに変換する。"""

    # 1. ISNULL(expr) → (expr) IS NULL
    #    ISNULLをIFNULLより先に処理（正規表現でIFNULLにマッチしないよう単語境界を使用）
    sql = _replace_func_call(sql, "ISNULL", lambda args: f"({args[0]}) IS NULL" if len(args) == 1 else f"ISNULL({', '.join(args)})")

    # 2. IIF(test, then, else [, unknown]) → CASE WHEN test THEN then ELSE else END
    #    IIFをIFより先に処理（IIFがIFにマッチしないよう）
    def _iif_replacer(args: List[str]) -> str:
        if len(args) >= 3:
            return f"CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END"
        return f"IIF({', '.join(args)})"
    sql = _replace_func_call(sql, "IIF", _iif_replacer)

    # 3. IFNULL(a, b) → COALESCE(a, b)
    sql = _replace_func_call(sql, "IFNULL", lambda args: f"COALESCE({', '.join(args)})")

    # 4. ZN(expr) → COALESCE(expr, 0)
    sql = _replace_func_call(sql, "ZN", lambda args: f"COALESCE({args[0]}, 0)" if len(args) == 1 else f"ZN({', '.join(args)})")

    # 5. IF expr THEN → CASE WHEN expr THEN / ELSEIF → WHEN
    #    IIF/IFNULL は既に変換済みなので、残っている IF は Tableau の IF/THEN 構文
    sql = re.sub(r"\bELSEIF\b", "WHEN", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bIF\b(?=.*\bTHEN\b)", "CASE WHEN", sql, flags=re.IGNORECASE)

    # 6. 単純な関数名/構文置換
    sql = re.sub(r"\bTODAY\s*\(\s*\)", "CURRENT_DATE", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bNOW\s*\(\s*\)", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)
    sql = _replace_func_call(sql, "LEN", lambda args: f"LENGTH({', '.join(args)})")
    sql = _replace_func_call(sql, "INT", lambda args: f"CAST({args[0]} AS INTEGER)" if len(args) == 1 else f"INT({', '.join(args)})")
    sql = _replace_func_call(sql, "FLOAT", lambda args: f"CAST({args[0]} AS FLOAT)" if len(args) == 1 else f"FLOAT({', '.join(args)})")
    sql = _replace_func_call(sql, "STR", lambda args: f"CAST({args[0]} AS VARCHAR)" if len(args) == 1 else f"STR({', '.join(args)})")

    return sql


def tableau_formula_to_omni_sql(
    formula: str,
    view_name: str,
    parameter_names: Optional[Set[str]] = None,
    param_internal_name_map: Optional[Dict[str, str]] = None,
    field_name_map: Optional[Dict[str, str]] = None,
) -> str:
    """
    Convert Tableau [Field] references to Omni ${view.field} references.
    Parameter references are converted to Mustache syntax:
      {{filters.<view_name>.<filter_name>.value}}

    Handles two patterns:
    - [Parameters].[InternalName] -> Mustache (Tableau cross-datasource parameter ref)
    - [ParamCaption] -> Mustache (simple caption-based ref)

    param_internal_name_map: maps internal param name (e.g. "パラメーター 1") to
      ASCII field key (e.g. "metrics_switch")
    field_name_map: maps raw caption/column name to the final ASCII field key.
      Falls back to snake() for unmapped names (e.g. database column names).
    """
    _param_names = parameter_names or set()
    _internal_map = param_internal_name_map or {}
    _field_map = field_name_map or {}

    sql = formula or ""

    # Convert Tableau-specific syntax/functions to standard SQL first
    sql = convert_tableau_syntax_to_sql(sql)

    # First, handle [Parameters].[InternalName] pattern (Tableau cross-DS param refs)
    def repl_params_dotted(m):
        internal_name = m.group(1)
        filter_name = _internal_map.get(internal_name, snake(internal_name))
        return "{{" + f"filters.{view_name}.{filter_name}.value" + "}}"

    sql = re.sub(r"\[Parameters\]\.\[([^\]]+)\]", repl_params_dotted, sql)

    # Then, handle remaining [Field] references
    def repl(m):
        raw = m.group(1)
        if raw in _param_names:
            key = _field_map.get(raw, snake(raw))
            return "{{" + f"filters.{view_name}.{key}.value" + "}}"
        key = _field_map.get(raw, snake(raw))
        return f"${{{view_name}.{key}}}"

    sql = re.sub(r"\[([^\]]+)\]", repl, sql)
    sql = re.sub(r"\bCOUNTD\s*\(", "count(distinct ", sql, flags=re.IGNORECASE)

    # Convert Tableau double-quoted string literals to SQL single quotes.
    # At this point, [Field] refs are already ${...} / {{...}}, so remaining
    # "..." are string literals from Tableau formulas.
    sql = re.sub(r'"([^"]*)"', r"'\1'", sql)

    return sql


def parse_table_ref(table_attr: str) -> Tuple[Optional[str], str]:
    """
    Tableau often encodes as [schema].[table] or [table] or schema.table
    Returns (schema_or_none, table)
    """
    t = (table_attr or "").strip().replace('"', "")
    parts = re.findall(r"\[([^\]]+)\]", t)
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if len(parts) == 1:
        return None, parts[0]
    if "." in t:
        ss = t.split(".")
        if len(ss) >= 2:
            return ss[-2], ss[-1]
    return None, t


def omni_view_name(schema: Optional[str], table: str) -> str:
    if schema:
        return f"{snake(schema)}__{snake(table)}"
    return snake(table)


def parse_tableau_lod(formula: str) -> Optional[dict]:
    """
    Parse Tableau LOD:
      { FIXED [Dim1], [Dim2] : SUM([Measure]) }
      { INCLUDE [Dim] : AVG([X]) }
      { EXCLUDE [Region] : AVG([Score]) }

    Returns:
      {
        grouping_strategy: fixed|always_include|always_exclude
        dims: [Dim1, Dim2]
        agg_func: sum|avg|countd|...
        inner_expr: expression inside agg(...)
      }
    """
    f = (formula or "").strip()
    m = re.match(r"^\{\s*(FIXED|INCLUDE|EXCLUDE)\s*(.*?)\s*:\s*(.*)\s*\}\s*$", f, flags=re.IGNORECASE)
    if not m:
        return None

    strat_raw = m.group(1).lower()
    dims_part = m.group(2) or ""
    expr_part = m.group(3) or ""

    grouping_strategy = LOD_GROUPING_MAP.get(strat_raw)
    if not grouping_strategy:
        return None

    dims = re.findall(r"\[([^\]]+)\]", dims_part)

    m2 = re.match(r"^\s*([A-Za-z]+)\s*\(\s*(.*)\s*\)\s*$", expr_part)
    if m2:
        agg = m2.group(1).lower()
        inner = m2.group(2)
    else:
        # 非典型LOD（ネストや複合式等）はここに落ちる
        agg = "sum"
        inner = expr_part

    return {
        "grouping_strategy": grouping_strategy,
        "dims": dims,
        "agg_func": agg,
        "inner_expr": inner,
    }


def datasource_display_name(ds: etree._Element, idx: int) -> str:
    return (ds.get("caption") or ds.get("name") or f"datasource_{idx}").strip()


def extract_datasources(root) -> List[etree._Element]:
    return root.findall(".//datasource")


def extract_table_relations(ds: etree._Element) -> List[Tuple[Optional[str], str]]:
    rels = []
    for rel in ds.findall(".//relation"):
        if rel.get("type") == "table" and rel.get("table"):
            schema, table = parse_table_ref(rel.get("table"))
            rels.append((schema, table))
    out = []
    seen = set()
    for s, t in rels:
        key = (s or "", t)
        if key not in seen:
            seen.add(key)
            out.append((s, t))
    return out


def extract_custom_sql_relations(ds: etree._Element) -> List[Tuple[str, str]]:
    """
    Return list of (name_hint, sql_text).
    Note: TWBの構造によってはSQLがattributeに入る/テキストに入る等ブレがあるため best effort。
    """
    out = []
    for rel in ds.findall(".//relation"):
        if rel.get("type") in {"text", "sql"}:
            sql_txt = text_or_none(rel)
            if sql_txt:
                name_hint = rel.get("name") or rel.get("caption") or "custom_sql"
                out.append((snake(name_hint), sql_txt))
    return out


def extract_calculated_fields(ds: etree._Element) -> List[Tuple[str, str, Optional[str], Optional[str]]]:
    """
    Return (caption_or_name, formula, datatype, internal_name)
    internal_name is the Tableau internal column name (e.g. "Calculation_1612753034899456")
    """
    out = []
    for col in ds.findall(".//column"):
        calc = col.find(".//calculation")
        if calc is None:
            continue
        # Skip parameter columns -- they are handled by extract_parameters_as_filters()
        if col.get("param-domain-type") is not None:
            continue
        # Skip group columns -- they are handled by extract_groups()
        if col.find(".//group") is not None:
            continue
        formula = calc.get("formula") or text_or_none(calc)
        if not formula:
            continue
        cap = col.get("caption") or col.get("name") or "calc"
        datatype = col.get("datatype")
        internal_name = (col.get("name") or "").strip("[]") or None
        out.append((cap, formula, datatype, internal_name))
    return out


@dataclass
class ParameterInfo:
    caption: str
    omni_type: str
    internal_name: Optional[str] = None  # e.g. "パラメーター 1" (from name attr, brackets stripped)
    default_value: Optional[str] = None
    allowed_values: Optional[List[str]] = None
    is_list: bool = False


def extract_parameters_as_filters(ds: etree._Element) -> List[ParameterInfo]:
    """
    Pragmatic detection: columns with param-domain-type attribute.
    Returns list of ParameterInfo with caption, type, default value, and allowed values.
    """
    out = []
    for col in ds.findall(".//column"):
        if col.get("param-domain-type") is None:
            continue
        cap = col.get("caption") or col.get("name") or "parameter"
        dt = (col.get("datatype") or "string").lower()
        if dt in {"integer", "real", "float", "number"}:
            t = "number"
        elif dt in {"date", "datetime"}:
            t = "timestamp"
        elif dt in {"boolean"}:
            t = "boolean"
        else:
            t = "string"

        # Extract default value (strip quotes)
        default_value = col.get("value")
        if default_value is not None:
            default_value = default_value.strip('"').strip("'")
            if t == "number":
                try:
                    # Preserve numeric form (int or float)
                    if "." in default_value:
                        default_value = str(float(default_value))
                    else:
                        default_value = str(int(default_value))
                except ValueError:
                    pass

        # Extract allowed values from <members> when param-domain-type="list"
        domain_type = col.get("param-domain-type") or ""
        is_list = domain_type == "list"
        allowed_values = None
        if is_list:
            members = col.findall(".//members/member")
            if members:
                allowed_values = []
                for member in members:
                    v = member.get("value")
                    if v is not None:
                        allowed_values.append(v.strip('"').strip("'"))

        # Internal name (strip brackets from name attr like "[パラメーター 1]")
        raw_name = col.get("name") or ""
        internal_name = raw_name.strip("[]") if raw_name else None

        out.append(ParameterInfo(
            caption=cap,
            omni_type=t,
            internal_name=internal_name,
            default_value=default_value,
            allowed_values=allowed_values,
            is_list=is_list,
        ))
    return out


@dataclass
class DatasourceFilter:
    """Represents a parsed Tableau datasource filter for Omni conversion."""
    column_name: str  # Raw column name extracted from TWB (e.g. "ORDER_DATETIME")
    filter_type: str  # "relative-date", "categorical", "quantitative"
    # relative-date specific
    first_period: Optional[int] = None
    last_period: Optional[int] = None
    period_type: Optional[str] = None  # "day", "month", "year", etc.
    include_future: bool = False
    include_null: bool = False


def extract_datasource_filters(ds: etree._Element) -> List[DatasourceFilter]:
    """
    Extract datasource-level filters from a Tableau datasource element.
    Currently supports class='relative-date' filters.
    Returns list of DatasourceFilter objects.
    """
    out = []
    for filt in ds.findall(".//filter"):
        fclass = filt.get("class") or ""
        if fclass == "relative-date":
            col_attr = filt.get("column") or ""
            # Extract column name from Tableau bracket notation
            # e.g. "[none:ORDER_DATETIME:qk]" -> "ORDER_DATETIME"
            col_match = re.match(r"^\[(?:[^:]*:)?([^:\]]+)(?::[^\]]*)?\]$", col_attr)
            if not col_match:
                continue
            col_name = col_match.group(1)

            first_period = int(filt.get("first-period") or "0")
            last_period = int(filt.get("last-period") or "0")
            period_type = filt.get("period-type-v2") or filt.get("period-type") or "day"
            include_future = (filt.get("include-future") or "false").lower() == "true"
            include_null = (filt.get("include-null") or "false").lower() == "true"

            out.append(DatasourceFilter(
                column_name=col_name,
                filter_type="relative-date",
                first_period=first_period,
                last_period=last_period,
                period_type=period_type,
                include_future=include_future,
                include_null=include_null,
            ))
    return out


def datasource_filter_to_omni(
    filt: DatasourceFilter,
    view_name: str,
    field_name_map: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Convert a DatasourceFilter to an always_where_sql clause string.
    Returns a SQL expression string or None if not convertible.

    For relative-date filters:
      first_period=-179, last_period=0, period_type=day
      -> ${view.field} >= DATEADD('day', -180, CURRENT_DATE())
    """
    _field_map = field_name_map or {}

    if filt.filter_type == "relative-date":
        col_key = _field_map.get(filt.column_name, snake(filt.column_name))
        qualified = f"${{{view_name}.{col_key}}}"

        first = filt.first_period or 0
        last = filt.last_period or 0
        # +1: Tableau の first_period/last_period は両端包含（例: -179〜0 = 今日含む180日間）
        duration = last - first + 1
        period = filt.period_type or "day"

        return f"{qualified} >= DATEADD('{period}', -{duration}, CURRENT_DATE())"

    return None


@dataclass
class TableauGroup:
    """Represents a Tableau group definition for Omni groups conversion."""
    caption: str           # Display name of the group column
    internal_name: str     # Tableau internal name e.g. "[Group 1]"
    base_field: str        # The field being grouped (from level= attribute)
    buckets: List[Tuple[str, List[str]]]  # [(group_name, [member_values])]


def extract_groups(ds: etree._Element) -> List[TableauGroup]:
    """
    Extract group definitions from <column> elements containing <group>.
    Tableau groups use <groupfilter function="union"> for each bucket
    and <groupfilter function="member"> for individual members.
    """
    out: List[TableauGroup] = []
    for col in ds.findall(".//column"):
        grp_elem = col.find(".//group")
        if grp_elem is None:
            continue
        caption = col.get("caption") or col.get("name") or "group"
        internal_name = (col.get("name") or "").strip("[]") or "group"

        base_field: Optional[str] = None
        buckets: List[Tuple[str, List[str]]] = []

        for union_filter in grp_elem.findall("groupfilter[@function='union']"):
            members: List[str] = []
            for member_filter in union_filter.findall("groupfilter[@function='member']"):
                member_val = member_filter.get("member")
                if member_val:
                    members.append(member_val)
                if base_field is None:
                    level = member_filter.get("level") or ""
                    level = level.strip("[]")
                    if level:
                        base_field = level
            if members:
                # Group name: use user:ui-marker or concatenate first member
                group_name = union_filter.get("user:ui-marker") or members[0]
                buckets.append((group_name, members))

        if not base_field or not buckets:
            continue

        out.append(TableauGroup(
            caption=caption,
            internal_name=internal_name,
            base_field=base_field,
            buckets=buckets,
        ))
    return out


def group_to_omni_dimension(
    group: TableauGroup,
    view_name: str,
    field_name_map: Optional[Dict[str, str]] = None,
) -> dict:
    """Convert a TableauGroup to an Omni dimension dict with groups syntax."""
    _field_map = field_name_map or {}
    base_key = _field_map.get(group.base_field, snake(group.base_field))
    omni_groups: List[dict] = []
    for group_name, members in group.buckets:
        omni_groups.append({
            "filter": {"is": members},
            "name": group_name,
        })
    return {
        "sql": f"${{{view_name}.{base_key}}}",
        "groups": omni_groups,
        "else": "Other",
        "label": group.caption,
    }


@dataclass
class TableauHierarchy:
    """Represents a Tableau drill-path hierarchy."""
    name: str              # Hierarchy name
    fields: List[str]      # Ordered list of raw field names (top → bottom)


def extract_hierarchies(ds: etree._Element) -> List[TableauHierarchy]:
    """Extract drill-path hierarchies from a datasource."""
    out: List[TableauHierarchy] = []
    for dp in ds.findall(".//drill-path"):
        name = dp.get("name") or "hierarchy"
        fields: List[str] = []
        for f in dp.findall("field"):
            raw = (f.text or "").strip().strip("[]")
            if raw:
                fields.append(raw)
        if len(fields) >= 2:
            out.append(TableauHierarchy(name=name, fields=fields))
    return out


def apply_hierarchies_to_dimensions(
    view: "OmniView",
    hierarchies: List[TableauHierarchy],
    field_name_map: Dict[str, str],
) -> None:
    """Add group_label and drill_fields to dimensions based on hierarchies."""
    for hier in hierarchies:
        group_label = hier.name.replace(" ", "_")
        for i, raw_field in enumerate(hier.fields):
            key = field_name_map.get(raw_field, snake(raw_field))
            # Create minimal dimension if not already present
            if key not in view.dimensions:
                view.dimensions[key] = {"sql": f'"{raw_field}"'}
            view.dimensions[key]["group_label"] = group_label
            # Add drill_fields pointing to next level (except last)
            if i < len(hier.fields) - 1:
                next_raw = hier.fields[i + 1]
                next_key = field_name_map.get(next_raw, snake(next_raw))
                view.dimensions[key]["drill_fields"] = [next_key]


def extract_joins_best_effort(ds: etree._Element) -> List[Tuple[str, str, str, Optional[str]]]:
    """
    Best-effort join extraction.
    Returns list of (left_table_ref, right_table_ref, join_type, on_sql_or_none)
    """
    joins = []
    for rel in ds.findall(".//relation[@type='join']"):
        jt = (rel.get("join") or "left").lower()
        join_type = {
            "left": "always_left",
            "inner": "inner",
            "full": "full_outer",
            "cross": "cross",
            "right": "right_left",
        }.get(jt, "always_left")

        children = rel.findall("./relation")
        if len(children) < 2:
            continue

        left = children[0].get("table") or children[0].get("name") or "left"
        right = children[1].get("table") or children[1].get("name") or "right"

        on_sql = None
        try:
            eq_exprs = rel.findall(".//clause[@type='join']//expression[@op='=']")
            parts = []
            for eq in eq_exprs:
                ex_children = eq.findall("./expression")
                if len(ex_children) == 2:
                    a = ex_children[0].get("op") or text_or_none(ex_children[0]) or ""
                    b = ex_children[1].get("op") or text_or_none(ex_children[1]) or ""
                    if a and b:
                        parts.append(f"{a} = {b}")
            if parts:
                on_sql = " AND ".join(parts)
        except Exception:
            on_sql = None

        joins.append((left, right, join_type, on_sql))
    return joins


# ----------------------------
# Topic join-tree helpers
# ----------------------------

def build_join_graph(rels: List[OmniRelationship]) -> Dict[str, Set[str]]:
    g: Dict[str, Set[str]] = {}
    for r in rels:
        g.setdefault(r.join_from_view, set()).add(r.join_to_view)
        g.setdefault(r.join_to_view, set()).add(r.join_from_view)
    return g


def build_topic_joins_tree(base: str, graph: Dict[str, Set[str]], allowed_views: Set[str], max_depth: int = 4) -> dict:
    """
    Build a simple BFS spanning tree for topic joins, restricted to allowed_views.
    """
    visited = {base}
    tree: dict = {}

    frontier = [(base, tree, 0)]
    while frontier:
        node, node_obj, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        for nbr in sorted(graph.get(node, set())):
            if nbr in visited:
                continue
            if nbr not in allowed_views:
                continue
            visited.add(nbr)
            node_obj[nbr] = {}
            frontier.append((nbr, node_obj[nbr], depth + 1))

    return tree


# ----------------------------
# Main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--twb", required=True, help="Path to Tableau .twb file")
    ap.add_argument("--out", required=True, help="Output directory root")
    ap.add_argument("--default-schema", default="PUBLIC", help="Schema to use if TWB omits schema")
    ap.add_argument("--topic-group-label", default=None, help="Optional group_label for all generated topics")
    ap.add_argument("--max-topic-join-depth", type=int, default=4, help="Max depth of joins tree per topic")
    ap.add_argument("--charts-out", default=None, help="Output directory for chart reproduction Markdown files")
    ap.add_argument("--domain-labels", default=None, help="Path to JSON file with domain labels: {\"worksheets\": {...}, \"dashboards\": {...}}")
    args = ap.parse_args()

    ensure_dir(args.out)
    views_dir = os.path.join(args.out, "views")
    topics_dir = os.path.join(args.out, "topics")
    ensure_dir(views_dir)
    ensure_dir(topics_dir)

    parser = etree.XMLParser(remove_blank_text=False, recover=True, huge_tree=True)
    root = etree.parse(args.twb, parser).getroot()

    omni_views: Dict[str, OmniView] = {}
    all_relationships: List[OmniRelationship] = []
    topics: List[OmniTopic] = []

    datasources = extract_datasources(root)

    # Pre-pass: build a global parameter registry from the "Parameters" datasource
    # (Tableau stores full parameter definitions with <members> only in this DS)
    global_param_registry: Dict[str, ParameterInfo] = {}
    for ds in datasources:
        ds_name_raw = (ds.get("name") or "").lower()
        if ds_name_raw == "parameters":
            for pinfo in extract_parameters_as_filters(ds):
                global_param_registry[pinfo.caption] = pinfo

    used_topic_names: Set[str] = set()
    # Collect field_name_map across all datasources for chart markdown generation
    global_field_name_map: Dict[str, str] = {}

    for idx, ds in enumerate(datasources):
        ds_name = datasource_display_name(ds, idx)
        base_topic_name = snake(ds_name)
        topic_name = base_topic_name
        # collision safety
        if topic_name in used_topic_names:
            topic_name = f"{base_topic_name}_{idx+1}"
        used_topic_names.add(topic_name)

        topic_label = ds_name

        ds_views: Set[str] = set()
        ds_relationships: List[OmniRelationship] = []

        # 1) Views from tables in this datasource
        tables = extract_table_relations(ds)
        for schema, table in tables:
            schema_final = schema or args.default_schema
            vname = omni_view_name(schema_final, table)
            ds_views.add(vname)
            if vname not in omni_views:
                omni_views[vname] = OmniView(name=vname, schema=schema_final, table_name=table)

        # 2) Views from custom SQL in this datasource
        custom_sql = extract_custom_sql_relations(ds)
        for name_hint, sql_txt in custom_sql:
            vname = f"{topic_name}__{name_hint}__sql"
            ds_views.add(vname)
            if vname not in omni_views:
                omni_views[vname] = OmniView(name=vname, schema=args.default_schema, sql=sql_txt)

        # Attach calcs/params to a reasonable view for this datasource
        attach_view = None
        if tables:
            schema_final, table = (tables[0][0] or args.default_schema), tables[0][1]
            attach_view = omni_view_name(schema_final, table)
        elif custom_sql:
            name_hint0, _sql0 = custom_sql[0]
            attach_view = f"{topic_name}__{name_hint0}__sql"

        # Pre-extract parameter names so formula conversion can use Mustache syntax
        ds_parameters = extract_parameters_as_filters(ds)
        # Merge global parameter registry (from "Parameters" DS) into local params
        # to fill in missing allowed_values/default_value from the canonical source
        for i, pinfo in enumerate(ds_parameters):
            if pinfo.caption in global_param_registry:
                gp = global_param_registry[pinfo.caption]
                if not pinfo.allowed_values and gp.allowed_values:
                    ds_parameters[i] = ParameterInfo(
                        caption=pinfo.caption,
                        omni_type=pinfo.omni_type,
                        default_value=pinfo.default_value or gp.default_value,
                        allowed_values=gp.allowed_values,
                        is_list=gp.is_list,
                    )
                elif pinfo.default_value is None and gp.default_value is not None:
                    ds_parameters[i] = ParameterInfo(
                        caption=pinfo.caption,
                        omni_type=pinfo.omni_type,
                        default_value=gp.default_value,
                        allowed_values=pinfo.allowed_values,
                        is_list=pinfo.is_list,
                    )
        param_captions: Set[str] = {p.caption for p in ds_parameters}

        # --- Build field_name_map (caption -> ASCII key) BEFORE formula conversion ---
        # This ensures all [Field] references in formulas resolve to the correct ASCII keys.
        field_name_map: Dict[str, str] = {}
        used_field_keys: Set[str] = set()

        # First, assign keys for parameters (filters)
        for pinfo in ds_parameters:
            key = make_field_key(pinfo.caption, pinfo.internal_name, used_field_keys)
            used_field_keys.add(key)
            field_name_map[pinfo.caption] = key

        # Then, assign keys for calculated fields
        calcs = extract_calculated_fields(ds) if (attach_view and attach_view in omni_views) else []
        calc_key_list: List[str] = []
        for cap, _formula, _datatype, internal_name in calcs:
            key = make_field_key(cap, internal_name, used_field_keys)
            used_field_keys.add(key)
            field_name_map[cap] = key
            calc_key_list.append(key)

        # Assign keys for group fields
        groups_list = extract_groups(ds) if (attach_view and attach_view in omni_views) else []
        group_key_list: List[str] = []
        for grp in groups_list:
            key = make_field_key(grp.caption, grp.internal_name, used_field_keys)
            used_field_keys.add(key)
            field_name_map[grp.caption] = key
            group_key_list.append(key)

        # Build mapping from internal param name to ASCII field key for
        # [Parameters].[InternalName] references in Tableau formulas
        param_internal_name_map: Dict[str, str] = {}
        # Include global params first, then override with local
        for p in global_param_registry.values():
            if p.internal_name:
                param_internal_name_map[p.internal_name] = field_name_map.get(p.caption, snake(p.caption))
        for p in ds_parameters:
            if p.internal_name:
                param_internal_name_map[p.internal_name] = field_name_map.get(p.caption, snake(p.caption))

        # 3) Calculated fields (including LOD) -> attach_view
        if attach_view and attach_view in omni_views:
            for calc_idx, (cap, formula, _datatype, _internal_name) in enumerate(calcs):
                fname = calc_key_list[calc_idx]
                label = cap

                if is_lod_formula(formula):
                    parsed = parse_tableau_lod(formula)
                    if parsed:
                        agg_func = parsed["agg_func"]
                        agg_type = AGG_FUNCS.get(agg_func)

                        grouping_key = parsed["grouping_strategy"]  # fixed / always_include / always_exclude
                        dims_snake = [snake(d) for d in parsed["dims"]]

                        inner_sql = tableau_formula_to_omni_sql(parsed["inner_expr"], attach_view, param_captions, param_internal_name_map, field_name_map)

                        lod_obj = {
                            "aggregate_type": (agg_type or "sum"),
                            grouping_key: dims_snake,
                            "cancel_query_filters": False,
                        }

                        if agg_func == "countd":
                            lod_obj["__todo__"] = "Tableau COUNTD in LOD: verify Omni aggregate_type (count_distinct vs distinct-on patterns)."

                        omni_views[attach_view].dimensions[fname] = {
                            "label": label,
                            "sql": inner_sql,
                            "level_of_detail": lod_obj,
                        }
                    else:
                        omni_views[attach_view].dimensions[fname] = {
                            "label": label,
                            "sql": tableau_formula_to_omni_sql(formula, attach_view, param_captions, param_internal_name_map, field_name_map),
                            "level_of_detail": {
                                "aggregate_type": "sum",
                                "fixed": [],
                                "cancel_query_filters": False,
                                "__todo__": "Could not parse Tableau LOD syntax; rewrite manually using Omni level_of_detail syntax.",
                            },
                        }
                    continue

                if is_aggregated_formula(formula):
                    # measure
                    unwrapped = try_unwrap_simple_aggregate(formula)
                    if unwrapped:
                        # 単純集計: AGG([field]) → sql は内部式のみ、aggregate_type を設定
                        agg_type, inner_expr = unwrapped
                        measure_obj = {
                            "label": label,
                            "sql": tableau_formula_to_omni_sql(inner_expr, attach_view, param_captions, param_internal_name_map, field_name_map),
                            "aggregate_type": agg_type,
                        }
                    else:
                        # 複雑集計: sql に集計関数を含む → aggregate_type を省略
                        measure_obj = {
                            "label": label,
                            "sql": tableau_formula_to_omni_sql(formula, attach_view, param_captions, param_internal_name_map, field_name_map),
                        }
                    omni_views[attach_view].measures[fname] = measure_obj
                else:
                    # dimension
                    omni_views[attach_view].dimensions[fname] = {
                        "label": label,
                        "sql": tableau_formula_to_omni_sql(formula, attach_view, param_captions, param_internal_name_map, field_name_map),
                    }

            # 4) Tableau Parameters -> Omni filter-only fields (filters:)
            for pinfo in ds_parameters:
                pname = field_name_map.get(pinfo.caption, snake(pinfo.caption))
                filter_obj: dict = {"type": pinfo.omni_type, "label": pinfo.caption}
                if pinfo.allowed_values:
                    filter_obj["suggestion_list"] = [{"value": v} for v in pinfo.allowed_values]
                    filter_obj["filter_single_select_only"] = True
                if pinfo.default_value is not None:
                    filter_obj["default_filter"] = {"is": pinfo.default_value}
                omni_views[attach_view].filters[pname] = filter_obj

            # 4b) Tableau Groups -> Omni groups dimensions
            for grp_idx, grp in enumerate(groups_list):
                gkey = group_key_list[grp_idx]
                dim_def = group_to_omni_dimension(grp, attach_view, field_name_map)
                omni_views[attach_view].dimensions[gkey] = dim_def

            # 4c) Tableau Hierarchies -> Omni group_label + drill_fields
            hierarchies = extract_hierarchies(ds)
            apply_hierarchies_to_dimensions(omni_views[attach_view], hierarchies, field_name_map)

        # 5) Joins -> relationships.yml (best effort)
        for left, right, join_type, on_sql in extract_joins_best_effort(ds):
            ls, lt = (None, left)
            rs, rt = (None, right)

            if left.startswith("[") and "]" in left:
                ls, lt = parse_table_ref(left)
            if right.startswith("[") and "]" in right:
                rs, rt = parse_table_ref(right)

            lv = omni_view_name((ls or args.default_schema), lt)
            rv = omni_view_name((rs or args.default_schema), rt)

            rel_obj = OmniRelationship(
                join_from_view=lv,
                join_to_view=rv,
                join_type=join_type,
                on_sql=on_sql or "/* TODO: parse join condition from TWB */ 1=1",
                relationship_type="assumed_many_to_one",
            )
            all_relationships.append(rel_obj)
            ds_relationships.append(rel_obj)

            # join 参照先が table relation として列挙されていなくても Topic の候補に含める
            ds_views.add(lv)
            ds_views.add(rv)

        # Build per-datasource topic
        base_view = None
        if attach_view and attach_view in ds_views:
            base_view = attach_view
        elif ds_views:
            base_view = sorted(ds_views)[0]

        t = OmniTopic(
            name=topic_name,
            label=topic_label,
            group_label=args.topic_group_label,
            base_view=base_view,
        )

        if base_view:
            graph = build_join_graph(ds_relationships)
            t.joins = build_topic_joins_tree(
                base_view,
                graph,
                allowed_views=ds_views,
                max_depth=args.max_topic_join_depth,
            )

        # 6) Datasource filters -> topic always_where_sql
        ds_filters = extract_datasource_filters(ds)
        where_clauses: List[str] = []
        for ds_filt in ds_filters:
            if attach_view:
                clause = datasource_filter_to_omni(ds_filt, attach_view, field_name_map)
                if clause:
                    where_clauses.append(clause)
        if where_clauses:
            t.always_where_sql = " AND ".join(where_clauses)

        # Merge field_name_map into global map for chart generation
        global_field_name_map.update(field_name_map)

        topics.append(t)

    # Write outputs
    for vname, v in omni_views.items():
        write_yaml(os.path.join(views_dir, f"{vname}.yaml"), v.to_yaml_obj())

    write_yaml(os.path.join(args.out, "relationships.yml"), [r.to_yaml_obj() for r in all_relationships])

    for t in topics:
        write_yaml(os.path.join(topics_dir, f"{t.name}.topic"), t.to_yaml_obj())

    # LOD参照フィールド警告: Topic fields: 指定時に漏れやすいフィールドを表示
    for vname, v in omni_views.items():
        lod_refs: Set[str] = set()
        for dim_name, dim_def in v.dimensions.items():
            if not isinstance(dim_def, dict):
                continue
            lod = dim_def.get("level_of_detail")
            if not isinstance(lod, dict):
                continue
            for lod_key in ("fixed", "always_include", "always_exclude"):
                for ref in (lod.get(lod_key) or []):
                    lod_refs.add(f"{vname}.{ref}")
        if lod_refs:
            print(f"  [!] {vname}: LOD参照フィールド（Topic fields:指定時に必ず含めること）: {sorted(lod_refs)}")

    print("Done.")
    print(f"- Views: {len(omni_views)} -> {views_dir}")
    print(f"- Relationships: {len(all_relationships)} -> {os.path.join(args.out, 'relationships.yml')}")
    print(f"- Topics (per datasource): {len(topics)} -> {topics_dir}")

    # Chart reproduction Markdown generation
    twb_stem = os.path.splitext(os.path.basename(args.twb))[0]
    charts_out = args.charts_out
    # Auto-compute output dir when --domain-labels is given but --charts-out is not
    if not charts_out and args.domain_labels:
        charts_out = os.path.join("migration-guides-twb2omni", twb_stem)
    if charts_out:
        import sys
        import json as _json
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from twb_chart_extractor import extract_worksheets, extract_dashboards
        from chart_markdown_gen import generate_chart_markdowns

        worksheets = extract_worksheets(root)
        dashboards = extract_dashboards(root)

        domain_labels = None
        if args.domain_labels:
            with open(args.domain_labels, "r", encoding="utf-8") as dl_f:
                domain_labels = _json.load(dl_f)

        generate_chart_markdowns(
            worksheets, dashboards, charts_out, global_field_name_map,
            domain_labels=domain_labels, twb_filename=twb_stem,
        )
        print(f"- Charts Markdown: {len(worksheets)} sheets + {len(dashboards)} dashboards -> {charts_out}")

    print("")
    print("IMPORTANT NEXT STEPS:")
    print("1) Fix relationships.yml: fill correct on_sql and relationship_type.")
    print("2) Verify each topic base_view and joins tree.")
    print("3) Verify LOD fields: fixed/include/exclude mapping and aggregate_type.")
    print("4) Consider adding primary_key fields in Omni views to prevent fan-outs.")
    if args.charts_out:
        print("5) Review charts/ Markdown files for accurate Omni reproduction steps.")


if __name__ == "__main__":
    main()
