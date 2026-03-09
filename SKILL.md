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
- `migration-guides-twb2omni/<twb_filename>/index.md` - 全体概要 + チャートタイプ対応表 + ダッシュボード構成
- `migration-guides-twb2omni/<twb_filename>/worksheet_<name>_<domain>.md` - シートごとの Omni 再現手順
- `migration-guides-twb2omni/<twb_filename>/dashboard_<name>_<domain>.md` - ダッシュボードごとの構成・再現方針

## Prerequisites

- Python 3.10+
- lxml / PyYAML

## Usage

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r ~/.claude/skills/tableau-twb-to-omni-semantic-layer-by-skill-creator/scripts/requirements.txt

python ~/.claude/skills/tableau-twb-to-omni-semantic-layer/scripts/twb_to_omni.py \
  --twb path/to/workbook.twb \
  --out omni_model_out \
  --charts-out migration-guides-twb2omni/workbook \
  --domain-labels domain_labels.json \
  --default-schema PUBLIC \
  --topic-group-label "Tableau Migrated"
```

### Options

| Flag | Description | Default |
|---|---|---|
| `--twb` | Path to Tableau .twb file | (required) |
| `--out` | Output directory for YAML | (required) |
| `--charts-out` | Output directory for chart Markdown files | `migration-guides-twb2omni/<twb_filename>/` (auto when `--domain-labels` specified) |
| `--domain-labels` | Path to JSON with domain labels (`{"worksheets": {...}, "dashboards": {...}}`) | (optional) |
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
- index.md のチャートタイプ対応が正しいか
- 各シート Markdown のフィールドマッピングが Omni フィールド名と一致しているか
- Custom Vega-Lite が必要なチャートを特定し、対応方針を決めたか
- ダッシュボード構成（含まれるシート）が正しいか
- ドメインラベル（ファイル名の日本語部分）が各シート/ダッシュボードの内容を適切に表しているか

### ドメインラベル JSON の生成ワークフロー
1. Claude が TWB 内のワークシート・ダッシュボード名とフィールド構成を分析
2. 各シート/ダッシュボードの分析ドメインを日本語ラベルとして推測（例: 遅延率推移、売上構成比）
3. `domain_labels.json` を生成:
   ```json
   {
     "worksheets": {"Sheet1": "遅延率推移", "Sheet2": "売上構成比"},
     "dashboards": {"Dashboard1": "運航概況"}
   }
   ```
4. `--domain-labels domain_labels.json` としてスクリプトに渡す

**Topic の label 形式**: `<ドメイン名>（Tableauデータソース：<datasource caption>）` とする。ドメイン名は日本語の分析ドメイン名（domain_labels.json から取得）、datasource caption は Tableau TWB の `<datasource caption="...">` 属性値をそのまま使用する

## Post-Processing Rules

### Anti-Patterns（Omni sync エラーになる禁止パターン）

#### エラー1: `dimensions:` ブロックの重複
view ファイルに `dimensions:` ブロックは **1つだけ** 許可される。LOD フィールド追加時は既存 `dimensions:` 内にマージすること。

```yaml
# BAD - dimensions: が2回出現 → YAML重複キーでsyncエラー
dimensions:
  order_id:
    sql: order_id
measures:
  total: ...
dimensions:          # ← 2つ目は禁止
  lod_field: ...

# GOOD - 1つの dimensions: に統合
dimensions:
  order_id:
    sql: order_id
  lod_field: ...
measures:
  total: ...
```

#### エラー2: `aggregate_type: number`
`number` は `filters:` の `type` であり `aggregate_type` の有効値ではない。SQL に集計関数を含む measure は `aggregate_type` 自体を省略する。

有効値: `sum`, `count`, `average`, `max`, `median`, `min`, `list`, `count_distinct`, `percentile`, `sum_distinct_on`, `average_distinct_on`, `median_distinct_on`, `percentile_distinct_on`

```yaml
# BAD
measures:
  gmv:
    sql: SUM("TOTAL_AMOUNT")
    aggregate_type: number    # ← 無効値

# GOOD - SQL に集計関数を含むので aggregate_type を省略
measures:
  gmv:
    sql: SUM("TOTAL_AMOUNT")
```

#### エラー3: Topic joins に `join_from_field` 等の文字列値
Topic の `joins:` にはビュー名のネストマップのみ許可される。`join_from_field` / `join_to_field` / `on_sql` / `join_via` は topic では使用不可（これらは `relationships.yml` に記載するもの）。

```yaml
# BAD - topic joins に relationship 用キーを混入
joins:
  view_a:
    join_from_field: "id"     # ← topic では不可
    join_to_field: "order_id" # ← topic では不可
    view_b: {}

# GOOD - topic joins は純粋なネスト構造のみ
joins:
  view_a:
    view_b: {}
```

#### エラー3b: Topic `joins` がリスト形式になっている
Topic の `joins:` はマップ（ネスト構造）でなければならない。リスト形式（`- join: view_name`）で記述すると `Property joins must be a map` エラーになる。join の詳細（`on_sql`, `type`, `relationship_type`）はトピックの `joins` ではなく `relationships.yml` に記載するもの。Topicでは joins の定義を省略しても問題ない。

```yaml
# BAD - リスト形式 → "Property joins must be a map" エラー
joins:
  - join: view_a
    type: left
    on_sql: ...
    relationship_type: many_to_one

# GOOD - ネストマップ形式
joins:
  view_a: {}
  view_b:
    view_c: {}

# GOOD - joins を省略（定義不要の場合）
# joins: は記述しない
```

#### エラー4: dimension に直接 `aggregate_type` を記述
`aggregate_type` は `measures` または `level_of_detail` ブロック内でのみ有効。dimension 直下に書くと sync エラーになる。LOD フィールドの場合は `level_of_detail.aggregate_type` にネストすること。

```yaml
# BAD - dimension に直接 aggregate_type → sync エラー
dimensions:
  store_avg_fulfillment_days:
    sql: '"FULFILLMENT_DAYS"'
    aggregate_type: average    # ← dimension 直下は不正
    level_of_detail:
      fixed: [store_key]

# GOOD - aggregate_type は level_of_detail 内にネスト
dimensions:
  store_avg_fulfillment_days:
    sql: '"FULFILLMENT_DAYS"'
    level_of_detail:
      aggregate_type: average  # ← 正しい位置
      fixed: [store_key]
```

#### エラー5: `time_for_duration` の形式不正
`time_for_duration` は必ず要素2つのリスト `[開始, 期間]` で指定する。スカラー値や要素1つ/3つ以上はエラー。

```yaml
# BAD - スカラー値
default_filters:
  orders.created_at:
    time_for_duration: "180 days ago"    # ← リストでない

# BAD - 要素1つ
default_filters:
  orders.created_at:
    time_for_duration:
      - "180 days ago"                   # ← 要素が1つだけ

# GOOD - 要素2つのリスト [開始, 期間]
default_filters:
  orders.created_at:
    time_for_duration: ["180 days ago", "180 days"]
```

**エラー6: パラメータが dimension と filter の両方に出力される**
```yaml
# BAD - パラメータが dimension にも出力されている
dimensions:
  parameter_date_range:
    sql: ...
filters:
  parameter_date_range:
    suggestion_list: ...

# GOOD - パラメータは filter のみ
filters:
  parameter_date_range:
    suggestion_list: ...
```

**エラー7: データソースフィルター期間の off-by-one**
```yaml
# BAD - 180日間なのに179日と計算（両端包含を忘れている）
default_filters:
  orders.created_at:
    time_for_duration: ["179 days ago", "179 days"]

# GOOD - 両端包含で +1 して正しい日数
default_filters:
  orders.created_at:
    time_for_duration: ["180 days ago", "180 days"]
```

#### エラー8: SQL文字列リテラルにダブルクォート使用
Tableau はダブルクォート `"受注"` で文字列を表現するが、SQL/Omni ではダブルクォートはカラム識別子を意味する。
文字列リテラルはシングルクォート `'受注'` を使うこと。

```yaml
# BAD - ダブルクォートはカラム識別子として解釈される
measures:
  order_status_count:
    sql: |-
      CASE WHEN ${view.status} = "受注" THEN 1 ELSE 0 END

# GOOD - 文字列リテラルはシングルクォート
measures:
  order_status_count:
    sql: |-
      CASE WHEN ${view.status} = '受注' THEN 1 ELSE 0 END
```

### 基本ルール

- **`aggregate_type: number` は絶対に使わない**: `number`はOmniの無効値。sqlに集計関数（SUM, COUNT, AVG等）を直接含むmeasureは `aggregate_type` 自体を省略する
- **Topic joinsに `join_via` は存在しない**: 間接joinは必ずネスト構造で表現する（`join_via` / `join_via_map` はtopicでは使用不可）
- **Topic joinsに `join_from_field` / `join_to_field` / `on_sql` は不可**: これらは `relationships.yml` 専用キー
- **Topic joinsのネストは直接relationship経路に従う**: `joins` 内の各ビューは、その親（トップレベルならbase_view）と `relationships.yml` で直接結合が定義されている必要がある
- **view ファイルの `dimensions:` / `measures:` / `filters:` ブロックは各1つのみ**: LODフィールド追加時も既存ブロックにマージする
- **`sample_queries` の `topic:` はトピック名（`.topic.yaml` のファイル名プレフィックス）を使用する**（`group_label` ではない）
- **Tableau LOD計算 -> Omni `level_of_detail` dimension**: 必ず`dimensions`セクションに配置する。measureに変換しない
- **`allowed_values` / `default` は使用不可**: `suggestion_list` / `default_filter` を使用する
- **filters構文**:
  - `suggestion_list` の各項目は `- value: <値>` 形式
  - `default_filter` は `is: <値>` 形式
  - `filter_single_select_only: true` を単一選択パラメータに設定
- **パラメータ参照はMustache構文**: `{{filters.<view>.<filter>.value}}`
- **`sample_queries` の `sorts`**: `- field: <field_name>` のみ有効
- **dimension に直接 `aggregate_type` を書かない**: `aggregate_type` は `measures` または `level_of_detail` ブロック内でのみ有効。LOD dimension の場合は `level_of_detail.aggregate_type` にネスト必須
- **`time_for_duration` は必ず2要素リスト**: `[開始, 期間]` 形式。スカラーや要素数不一致は sync エラー。例: `time_for_duration: ["180 days ago", "180 days"]`
- **LOD `fixed` 参照フィールドも Topic `fields:` に含める**: LOD dimension の `level_of_detail.fixed` / `always_include` / `always_exclude` で参照されるフィールドは、view 内フィールドとして解決される。Topic で `fields:` を明示する場合、これらのフィールドも `base_view.field_name` として含めること。欠落すると「fields are outside of the topic」エラーになる
  ```yaml
  # 同一 view 内フィールド参照（fixed に素のフィールド名）
  store_avg_fulfillment_days:
    sql: ...
    level_of_detail:
      aggregate_type: average
      fixed: [ store_key ]          # → 同 view の store_key

  # 他 view のフィールド参照（fixed に view_name.field_name）
  region_avg_fulfillment_days:
    sql: |-
      CASE WHEN ${omni_dbt_dwh__fact_order_lifecycle.order_status} = '配送完了'
        THEN ${omni_dbt_dwh__fact_order_lifecycle.total_fulfillment_days} END
    label: 地域別_平均フルフィル日数
    level_of_detail:
      aggregate_type: average
      fixed: [ omni_dbt_dwh__dim_store.region ]
  ```
- **Tableau データソースフィルター → Topic `default_filters`**: TWB の `<filter class="relative-date">` を Topic の `default_filters` に `time_for_duration` 形式で変換すること。Topic に `default_filters` がないとデータソースフィルターが適用されない
- **Topic `fields:` リストには join した dim view の全フィールドを含める**: dim view のフィールドを Tableau で使用したものだけに絞ると、LOD の `fixed` キーや join キーなど内部参照フィールドが欠落し sync エラーになる。`fields:` を省略すれば全フィールドが公開されるが、明示的に指定する場合は join した各 view の全フィールドを含めること
- **パラメータは filter のみに出力する**: パラメータを dimension や measure に変換してはならない。パラメータは常に `filters:` セクションのみに配置する
- **SQL文字列リテラルはシングルクォート**: Tableau はダブルクォート `"受注"` で文字列を表現するが、SQL/Omni ではダブルクォートはカラム識別子。計算フィールドの文字列リテラルは必ずシングルクォート `'受注'` を使う
- **データソースフィルター期間は両端包含（+1）、手動変更禁止**: スクリプトが出力した `time_for_duration` の日数を手動で変更しない。両端包含で `last - first + 1` が正しい計算

### Validation

ポスト処理後、以下のバリデーションスクリプトを **必ず** 実行してエラーがないことを確認する:

```bash
python ~/.claude/skills/tableau-twb-to-omni-semantic-layer/scripts/validate_omni_yaml.py --dir <output_directory>
```

## References

- [references/omni-yaml-primer.md](references/omni-yaml-primer.md) - Omni YAML 構文リファレンス
- [references/object-mapping.md](references/object-mapping.md) - Tableau -> Omni オブジェクト対応表
- [references/limitations.md](references/limitations.md) - 制限事項
- [references/chart-type-mapping.md](references/chart-type-mapping.md) - チャートタイプ対応表
