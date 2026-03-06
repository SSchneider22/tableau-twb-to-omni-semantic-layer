---
name: tableau-twb-to-omni-semantic-layer
description: Convert Tableau .twb (XML workbook) into Omni semantic layer YAML (views, relationships, topics) AND generate chart reproduction Markdown guides. Generates one Omni Topic per Tableau Datasource, plus per-sheet Markdown with Omni reproduction steps.
---

# Tableau TWB -> Omni Semantic Layer + Chart Reproduction Migrator

## Overview

Tableau の `.twb`（XML）から、Omni の Semantic Layer YAML とチャート再現用 Markdown ガイドを生成します。

生成物:
- `views/*.yaml` - Omni View 定義
- `relationships.yml` - View 間の join 定義
- `topics/*.topic` - Tableau Datasource ごとに 1 つの Topic
- `charts/index.md` - 全体概要 + チャートタイプ対応表 + ダッシュボード構成
- `charts/<sheet_name>.md` - シートごとの Omni 再現手順

## Prerequisites

- Python 3.10+
- lxml / PyYAML

## Usage

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r ~/.claude/skills/tableau-twb-to-omni-semantic-layer-by-skill-creator/scripts/requirements.txt

python ~/.claude/skills/tableau-twb-to-omni-semantic-layer-by-skill-creator/scripts/twb_to_omni.py \
  --twb path/to/workbook.twb \
  --out omni_model_out \
  --charts-out omni_model_out/charts \
  --default-schema PUBLIC \
  --topic-group-label "Tableau Migrated"
```

### Options

| Flag | Description | Default |
|---|---|---|
| `--twb` | Path to Tableau .twb file | (required) |
| `--out` | Output directory for YAML | (required) |
| `--charts-out` | Output directory for chart Markdown files | (optional) |
| `--default-schema` | Schema name when TWB omits it | PUBLIC |
| `--topic-group-label` | group_label for all generated topics | (none) |
| `--max-topic-join-depth` | Max depth of joins tree per topic | 4 |

## Post-Generation Checklist

### Semantic Layer YAML
- relationships.yml
  - on_sql が TODO になっていないか
  - relationship_type（many_to_one など）を正しく設定したか
- 各 topics/*.topic
  - base_view が妥当か（その datasource の主ファクトになっているか）
  - joins が出しすぎになっていないか（不要 join を削る）
- LOD（{FIXED|INCLUDE|EXCLUDE ...}）
  - level_of_detail の fixed/always_include/always_exclude が想定通りか
  - aggregate_type が想定通りか
- Parameters
  - filter-only fields（filters:）として作られた型が妥当か
- 必要に応じて primary key の定義や distinct-on 集計の設計を追加する

### Chart Reproduction Markdown
- charts/index.md のチャートタイプ対応が正しいか
- 各シート Markdown のフィールドマッピングが Omni フィールド名と一致しているか
- Custom Vega-Lite が必要なチャートを特定し、対応方針を決めたか
- ダッシュボード構成（含まれるシート）が正しいか

## Post-Processing Rules

- **`aggregate_type: number` は絶対に使わない**: `number`はOmniの無効値。sqlに集計関数（SUM, COUNT, AVG等）を直接含むmeasureは `aggregate_type` 自体を省略する
- **Topic joinsに `join_via` は存在しない**: 間接joinは必ずネスト構造で表現する（`join_via` / `join_via_map` はtopicでは使用不可）
- **Topic joinsのネストは直接relationship経路に従う**: `joins` 内の各ビューは、その親（トップレベルならbase_view）と `relationships.yml` で直接結合が定義されている必要がある
- **`sample_queries` の `topic:` は `group_label`（日本語Topic名）を使用する**
- **Tableau LOD計算 -> Omni `level_of_detail` dimension**: 必ず`dimensions`セクションに配置する。measureに変換しない
- **`allowed_values` / `default` は使用不可**: `suggestion_list` / `default_filter` を使用する
- **filters構文**:
  - `suggestion_list` の各項目は `- value: <値>` 形式
  - `default_filter` は `is: <値>` 形式
  - `filter_single_select_only: true` を単一選択パラメータに設定
- **パラメータ参照はMustache構文**: `{{filters.<view>.<filter>.value}}`
- **`sample_queries` の `sorts`**: `- field: <field_name>` のみ有効
- **Tableauデータソースフィルター（相対日付）-> Omni `default_filters`**: `time_for_duration` に変換

## References

- [references/omni-yaml-primer.md](references/omni-yaml-primer.md) - Omni YAML 構文リファレンス
- [references/object-mapping.md](references/object-mapping.md) - Tableau -> Omni オブジェクト対応表
- [references/limitations.md](references/limitations.md) - 制限事項
- [references/chart-type-mapping.md](references/chart-type-mapping.md) - チャートタイプ対応表
