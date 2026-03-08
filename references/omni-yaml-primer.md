# Omni YAML Primer（最小）

## View（table-backed）
name: ecomm__orders
schema: ECOMM
table_name: orders

dimensions:
  order_id:
    sql: order_id

measures:
  total_revenue:
    sql: ${ecomm__orders.revenue}
    aggregate_type: sum

filters:
  status_param:
    type: string
    label: Status Filter
    suggestion_list:
      - value: Active
        label: アクティブ          # optional: UIに表示するラベル
      - value: Inactive
    default_filter:
      is: Active
    filter_single_select_only: true

## Filter参照（Mustache構文）
SQL内でfilterの値を参照するにはMustache構文を使用する（Lookerの `{% parameter %}` は使用不可）:
```yaml
dimensions:
  status_check:
    sql: |
      CASE WHEN "STATUS" = {{filters.ecomm__orders.status_param.value}}
      THEN 'Match' ELSE 'No Match' END
```

デフォルト値付きMustache構文（filterが未選択の場合のフォールバック）:
```
{{^filters.ecomm__orders.status_param.value}}'Active'{{/filters.ecomm__orders.status_param.value}}
```

## relationships.yml

join_type 有効値: `always_left`(デフォルト), `inner`, `full_outer`, `cross`, `right_left`, `left_right`

- join_from_view: ecomm__orders
  join_to_view: ecomm__users
  join_type: always_left
  on_sql: ${ecomm__orders.user_id} = ${ecomm__users.id}
  relationship_type: many_to_one
  reversible: false              # trueにすると逆方向のjoinも自動生成
  where_sql: ${ecomm__users.is_active} = true   # join先に追加フィルタ条件
  # join_from_view_as / join_to_view_as: 同一viewへの複数joinでエイリアスを指定

## Topic（datasource ごとに 1つ）
label: Orders DS
group_label: Tableau Migrated
base_view: ecomm__orders
fields:                              # 省略すると全フィールドが公開される
  [                                  # 明示する場合は join した各 view の全フィールドを含めること
    ecomm__orders.order_id,          # LOD の fixed キーや join キーが欠落すると sync エラーになる
    ecomm__users.id,
  ]
joins:
  ecomm__users: {}

## default_filters（time_for_duration） — Tableau データソースフィルターの変換先

Tableau の `<filter class="relative-date">` （データソースフィルター）は Topic の `default_filters` に変換する。`time_for_duration` を使用し、値は必ず **2要素リスト** `[開始, 期間]` で指定する。

```yaml
# Topic ファイル内
default_filters:
  ecomm__orders.created_at:
    time_for_duration: ["180 days ago", "180 days"]
  ecomm__orders.shipped_at:
    time_for_duration: ["30 days ago", "30 days"]
```

## aggregate_type

有効値: `sum`, `count`, `average`, `max`, `median`, `min`, `list`, `count_distinct`, `percentile`, `sum_distinct_on`, `average_distinct_on`, `median_distinct_on`, `percentile_distinct_on`

**重要**: `aggregate_type` を指定するとOmniの**symmetric aggregation**（joinファンアウト防止の自動最適化）が有効になるため、可能な限り `aggregate_type` を使用することを推奨する。

sql に集計関数（SUM(), COUNT(), AVG(), COUNT(DISTINCT ...) 等）を直接記述している measure は `aggregate_type` を省略すること。ただし、SQL直書きの場合はsymmetric aggregation最適化が無効になるトレードオフがある。

```yaml
# 推奨: sql がカラム参照のみ → aggregate_type を指定（symmetric aggregation有効）
measures:
  total_revenue:
    sql: '"REVENUE"'
    aggregate_type: sum

# OK: sql に集計関数を含む → aggregate_type を省略（symmetric aggregation無効）
measures:
  gmv:
    sql: SUM("TOTAL_AMOUNT") + SUM("SHIPPING_FEE")
```

## Topic joins（ネスト構造）

Topicのjoinsは**ネスト構造**で間接joinパスを表現する（`join_via` パラメータはtopicには存在しない）。
ネストの深さがjoinの経由パスを表す。末端のviewは `{}` で終端する。

```yaml
joins:
  direct_view_a:              # base_viewから直接join
    indirect_view_b: {}       # direct_view_a 経由でjoin
  direct_view_c: {}           # base_viewから直接join
```

**topic joins で使用不可なキー**:
- `join_from_field` — relationships.yml 専用
- `join_to_field` — relationships.yml 専用
- `join_via` — topic には存在しない（ネスト構造で表現）
- `join_via_map` — query_views 専用パラメータ
- `on_sql` — relationships.yml 専用

topic joins 内の全値は dict（マップ）でなければならない。文字列値が含まれると sync エラーになる。

## LOD（level_of_detail）例

**重要**: LODフィールドは必ず `dimensions` セクションに配置する（measuresではない）。`sql` には集計関数を含めず、集計は `level_of_detail.aggregate_type` で指定する。

```yaml
# Tableau { FIXED [STORE_KEY] : AVG(IF [STATUS]='完了' THEN [DAYS] END) } に相当
dimensions:
  avg_days_by_store:
    label: 店舗別平均日数
    sql: CASE WHEN "STATUS" = '完了' THEN "DAYS" END
    level_of_detail:
      aggregate_type: average
      fixed: [store_key]
      cancel_query_filters: false

# Tableau { INCLUDE [USER_ID] : SUM([PRICE]) } に相当
dimensions:
  customer_lifetime_spend:
    label: Customer Lifetime Spend
    sql: ${ecomm__orders.sale_price}
    level_of_detail:
      aggregate_type: sum
      always_include: [user_id]
      cancel_query_filters: false

# *_distinct_on aggregate types 使用時は custom_primary_key_sql が必須
dimensions:
  distinct_revenue:
    sql: ${ecomm__orders.revenue}
    level_of_detail:
      aggregate_type: sum_distinct_on
      custom_primary_key_sql: ${ecomm__orders.order_id}
      fixed: [store_key]
```

## sample_queries

sample_queries:
  クエリ名:
    query:
      fields: [view_name.field1, view_name.field2]
      base_view: view_name
      calculations:                # optional: インラインの計算フィールド定義
        calc_field_name:
          sql: ${view_name.field1} + ${view_name.field2}
      sorts:
        - field: view_name.field1
      limit: 100
      topic: topic_name          # ← トピック名（.topic.yaml のファイル名プレフィックス）を使用
    description: クエリの説明
    prompt: ユーザーが聞く質問例
    hidden: false                  # trueにするとUIから非表示（AI用途のみ等）
    ai_context: このクエリの補足説明（AIが回答生成時に参照）
    exclude_from_ai_context: false # trueにするとAIコンテキストから除外
