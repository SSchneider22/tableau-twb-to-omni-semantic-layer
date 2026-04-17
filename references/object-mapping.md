# Tableau → Omni マッピング（要点）
この Skill は「Semantic Layer（views / relationships / topics）」の生成にフォーカスします。
## 1) Datasource / Topic
- Tableau Datasource → Omni Topic（1 datasource = 1 topic）
  - base_view: datasource 内で最初に見つかった table（なければ最初の custom SQL view）
  - joins: datasource 内で検出できた join から最大深さ4でツリーを自動生成
## 2) Relation → View
- relation@type="table" → Omni View（schema + table_name）
- relation@type="text"/"sql" → Omni View（sql: | ...）
## 3) Join → Relationship
- relation@type="join" → relationships.yml
  - join_type: `always_left`(デフォルト), `inner`, `full_outer`, `cross`, `right_left`, `left_right` を可能な範囲で変換
  - on_sql: 解析できない場合は TODO
  - relationship_type: 自動推定困難のため assumed_many_to_one（要手修正）
## 4) Field → dimension / measure
- Calculated field:
  - 集計関数を含む → measure（sql + aggregate_type 可能なら設定）
  - **SQLに集計関数（SUM, COUNT, AVG, COUNT DISTINCT 等）が直接含まれる場合は aggregate_type を省略する**（Omniがsql内の集計関数を自動認識するため）
  - 集計関数を含まない単純カラム参照の measure → aggregate_type を明示（sum, count 等）。aggregate_type指定によりsymmetric aggregation（joinファンアウト防止）が有効になるため推奨
  - SQL直書きの集計measure → symmetric aggregation最適化が無効になるトレードオフがある
  - それ以外 → dimension（sql）
- Tableau LOD:
  - { FIXED ... : AGG(...) } → dimension + level_of_detail.fixed
  - { INCLUDE ... : AGG(...) } → dimension + level_of_detail.always_include
  - { EXCLUDE ... : AGG(...) } → dimension + level_of_detail.always_exclude
## 4-b) Topic内 joins のネスト構造
- Topicのjoinsは**ネスト構造**で間接joinパスを表現する（`join_via` パラメータはtopicには存在しない）
- ネストの深さがjoinの経由パスを表し、末端のviewは `{}` で終端する
- 例: view_a経由でview_bにjoinする場合 → `joins:\n  view_a:\n    view_b: {}`

## 5) Datasource Filter → always_where_sql
- Tableau `<filter class="relative-date">` (データソースフィルター) → Topic `always_where_sql`
  - 例: `always_where_sql: ${view.created_at} >= DATEADD('day', -180, CURRENT_DATE())`
  - 複数フィルターがある場合は `AND` で結合

## 6) Group → groups dimension
- Tableau `<group>` (inside `<column>`) → Omni dimension with `groups` syntax
  - 各 `<groupfilter function="union">` → `groups` リストの1エントリ（`filter.is` + `name`）
  - `else: Other` でグループ外の値をカバー

## 7) Hierarchy → group_label + drill_fields
- Tableau `<drill-path>` → 各フィールドに `group_label` + `drill_fields` を付与
  - 同じ階層内のフィールドは共通の `group_label` を持つ
  - 各フィールドの `drill_fields` に次のレベルのフィールドを設定（最下位は drill_fields なし）

## 8) Parameter → filter-only fields
- Tableau Parameter は view の `filters:` に filter-only field として作成
- マッピング詳細:
  - `caption` → `label`
  - `datatype` → `type`（string / number / timestamp / boolean に変換）
  - `param-domain-type="list"` + `<members>` → `suggestion_list`（各値を `- value:` で列挙）
  - `value` 属性 → `default_filter: { is: <value> }`
  - リスト型パラメータ → `filter_single_select_only: true`
- SQL参照: `{% parameter %}` (Looker構文) ではなく `{{filters.<view>.<filter>.value}}` (Mustache構文) を使用
- **`allowed_values` / `default` キーはOmniに存在しない** → `suggestion_list` / `default_filter` を使う