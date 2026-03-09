#!/usr/bin/env python3
"""Omni YAML バリデーションスクリプト.

生成された Omni semantic layer YAML を検証し、
Omni File Sync でエラーになる既知のパターンを検出する。

Usage:
    python validate_omni_yaml.py --dir <output_directory>
"""

import argparse
import re
import sys
from pathlib import Path

import yaml


VALID_AGGREGATE_TYPES = {
    "sum",
    "count",
    "average",
    "max",
    "median",
    "min",
    "list",
    "count_distinct",
    "percentile",
    "sum_distinct_on",
    "average_distinct_on",
    "median_distinct_on",
    "percentile_distinct_on",
}

TOPIC_INVALID_KEYS = {
    "join_via",
    "join_via_map",
    "join_from_field",
    "join_to_field",
    "on_sql",
}


def check_duplicate_top_level_keys(file_path: Path) -> list[str]:
    """view ファイルのトップレベルキー重複を検出する."""
    errors = []
    text = file_path.read_text(encoding="utf-8")
    key_pattern = re.compile(r"^([a-z_]+):\s*$", re.MULTILINE)
    seen: dict[str, int] = {}
    for match in key_pattern.finditer(text):
        key = match.group(1)
        line_no = text[: match.start()].count("\n") + 1
        if key in seen:
            errors.append(
                f"{file_path}:{line_no}: duplicate top-level key '{key}:' "
                f"(first at line {seen[key]})"
            )
        else:
            seen[key] = line_no
    return errors


def check_aggregate_types(file_path: Path) -> list[str]:
    """aggregate_type の値が有効値セットに含まれるか検証する."""
    errors = []
    text = file_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        errors.append(f"{file_path}: YAML parse error")
        return errors
    if not isinstance(data, dict):
        return errors

    def _walk(obj: dict, path: str = "") -> None:
        for key, val in obj.items():
            current = f"{path}.{key}" if path else key
            if key == "aggregate_type" and isinstance(val, str):
                if val not in VALID_AGGREGATE_TYPES:
                    errors.append(
                        f"{file_path}: invalid aggregate_type '{val}' "
                        f"at {current}"
                    )
            elif isinstance(val, dict):
                _walk(val, current)

    _walk(data)
    return errors


def check_topic_joins(file_path: Path) -> list[str]:
    """topic の joins 内に不正なキーや文字列値がないか検証する."""
    errors = []
    text = file_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        errors.append(f"{file_path}: YAML parse error")
        return errors
    if not isinstance(data, dict):
        return errors

    joins = data.get("joins")
    if joins is None:
        return errors
    if not isinstance(joins, dict):
        errors.append(
            f"{file_path}: 'joins' must be a map (got {type(joins).__name__}). "
            f"Use nested view map structure, not a list. "
            f"If join details (on_sql, type, relationship_type) are needed, "
            f"use 'relationships' parameter instead of 'joins'"
        )
        return errors

    def _check_joins(obj: dict, path: str = "joins") -> None:
        for key, val in obj.items():
            current = f"{path}.{key}"
            if key in TOPIC_INVALID_KEYS:
                errors.append(
                    f"{file_path}: invalid key '{key}' in topic joins "
                    f"at {current} (belongs in relationships.yml)"
                )
            if isinstance(val, str):
                errors.append(
                    f"{file_path}: string value for '{key}' in topic joins "
                    f"at {current} (must be a dict/map)"
                )
            elif isinstance(val, dict):
                _check_joins(val, current)
            elif val is not None:
                errors.append(
                    f"{file_path}: unexpected value type for '{key}' in topic "
                    f"joins at {current} (must be a dict/map or empty)"
                )

    _check_joins(joins)
    return errors


def check_aggregate_type_on_dimensions(file_path: Path) -> list[str]:
    """dimension に直接 aggregate_type が付いている場合を検出する.

    aggregate_type は measures または level_of_detail 内でのみ有効。
    dimensions 直下のフィールドに直接 aggregate_type があると sync エラーになる。
    """
    errors = []
    text = file_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return errors
    if not isinstance(data, dict):
        return errors

    dims = data.get("dimensions")
    if not isinstance(dims, dict):
        return errors

    for field_name, field_def in dims.items():
        if not isinstance(field_def, dict):
            continue
        if "aggregate_type" in field_def:
            # aggregate_type inside level_of_detail is fine
            lod = field_def.get("level_of_detail")
            if isinstance(lod, dict) and "aggregate_type" in lod:
                # The direct aggregate_type is the problem
                pass
            errors.append(
                f"{file_path}: dimension '{field_name}' has direct "
                f"'aggregate_type' (must be inside 'level_of_detail' or "
                f"moved to measures)"
            )

    return errors


def check_time_for_duration(file_path: Path) -> list[str]:
    """topic の default_filters 内で time_for_duration が2要素リストでない場合を検出する."""
    errors = []
    text = file_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return errors
    if not isinstance(data, dict):
        return errors

    default_filters = data.get("default_filters")
    if not isinstance(default_filters, dict):
        return errors

    for filter_name, filter_def in default_filters.items():
        if not isinstance(filter_def, dict):
            continue
        tfd = filter_def.get("time_for_duration")
        if tfd is None:
            continue
        if not isinstance(tfd, list) or len(tfd) != 2:
            errors.append(
                f"{file_path}: default_filters.{filter_name}."
                f"time_for_duration must be a 2-element list "
                f"[start, duration], got: {tfd!r}"
            )

    return errors


def check_topic_lod_field_coverage(
    topic_path: Path, views_dir: Path
) -> list[str]:
    """Topic の fields: が明示されている場合、base_view の LOD 参照フィールドが含まれているか検証する."""
    errors = []
    text = topic_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return errors
    if not isinstance(data, dict):
        return errors

    fields_list = data.get("fields")
    if not isinstance(fields_list, list) or not fields_list:
        return errors  # fields: 未指定なら全フィールド公開 → チェック不要

    base_view = data.get("base_view")
    if not base_view:
        return errors

    # base_view の view ファイルを読み込む
    view_file = views_dir / f"{base_view}.yaml"
    if not view_file.exists():
        return errors

    try:
        view_data = yaml.safe_load(view_file.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return errors
    if not isinstance(view_data, dict):
        return errors

    # LOD 参照フィールドを収集
    lod_refs: set[str] = set()
    dims = view_data.get("dimensions")
    if isinstance(dims, dict):
        for dim_name, dim_def in dims.items():
            if not isinstance(dim_def, dict):
                continue
            lod = dim_def.get("level_of_detail")
            if not isinstance(lod, dict):
                continue
            for lod_key in ("fixed", "always_include", "always_exclude"):
                for ref in (lod.get(lod_key) or []):
                    lod_refs.add(f"{base_view}.{ref}")

    if not lod_refs:
        return errors

    fields_set = set(fields_list)
    missing = sorted(lod_refs - fields_set)
    for m in missing:
        errors.append(
            f"{topic_path}: LOD参照フィールド '{m}' が fields: に含まれていません "
            f"(level_of_detail の fixed/always_include/always_exclude で参照されています)"
        )

    return errors


def check_sql_double_quoted_literals(file_path: Path) -> list[str]:
    """計算フィールドの SQL 内にダブルクォート文字列リテラルが残っていないか検出する.

    Tableau はダブルクォートで文字列リテラルを表現するが、SQL/Omni では
    ダブルクォートはカラム識別子を意味する。計算フィールド（${...} を含む sql）
    内の "..." は文字列リテラルの可能性が高く、シングルクォートにすべき。
    """
    errors = []
    text = file_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return errors
    if not isinstance(data, dict):
        return errors

    # Pattern to find double-quoted strings that look like string literals
    # (not column identifiers like "COLUMN_NAME" which are all-caps/underscored)
    dq_pattern = re.compile(r'"([^"]+)"')
    col_id_pattern = re.compile(r'^[A-Z_][A-Z0-9_]*$')

    for section_name in ("dimensions", "measures"):
        section = data.get(section_name)
        if not isinstance(section, dict):
            continue
        for field_name, field_def in section.items():
            if not isinstance(field_def, dict):
                continue
            sql_val = field_def.get("sql")
            if not isinstance(sql_val, str):
                continue
            # Only check calculated fields (those referencing other fields)
            if "${" not in sql_val and "{{" not in sql_val:
                continue
            for m in dq_pattern.finditer(sql_val):
                inner = m.group(1)
                # Skip column identifiers (ALL_CAPS_UNDERSCORE)
                if col_id_pattern.match(inner):
                    continue
                errors.append(
                    f"{file_path}: {section_name}.{field_name} の sql に"
                    f"ダブルクォート文字列 \"{inner}\" があります"
                    f"（シングルクォート '{inner}' にすべき）"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate generated Omni YAML files"
    )
    parser.add_argument(
        "--dir", required=True, help="Output directory to validate"
    )
    args = parser.parse_args()

    base = Path(args.dir)
    if not base.is_dir():
        print(f"Error: {base} is not a directory", file=sys.stderr)
        return 1

    all_errors: list[str] = []

    # views/*.yaml
    views_dir = base / "views"
    if views_dir.is_dir():
        for f in sorted(views_dir.glob("*.yaml")):
            all_errors.extend(check_duplicate_top_level_keys(f))
            all_errors.extend(check_aggregate_types(f))
            all_errors.extend(check_aggregate_type_on_dimensions(f))
            all_errors.extend(check_sql_double_quoted_literals(f))

    # topics/*.topic
    topics_dir = base / "topics"
    if topics_dir.is_dir():
        for f in sorted(topics_dir.glob("*.topic")):
            all_errors.extend(check_topic_joins(f))
            all_errors.extend(check_time_for_duration(f))
            if views_dir.is_dir():
                all_errors.extend(check_topic_lod_field_coverage(f, views_dir))

    if all_errors:
        print(f"Found {len(all_errors)} error(s):\n")
        for err in all_errors:
            print(f"  - {err}")
        return 1

    print("Validation passed: no errors found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
