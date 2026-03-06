# Tableau Mark Type -> Omni Visualization Mapping

## Chart Type Mapping

| Tableau Mark Class | Omni Equivalent | Omni 設定方法 | 制限事項 |
|---|---|---|---|
| Automatic (時間 x measure) | Line chart | X軸に timestamp dimension、Y軸に measure | |
| Automatic (カテゴリ x measure) | Bar chart | dimension と measure を選択 | |
| Bar | Bar chart (grouped/stacked/stack%) | Series Config で Stack/Group 選択 | |
| Line | Line chart | temporal axis 推奨 | |
| Area | Area chart | Color facet で stacked area | |
| Text | Table | Pivot 機能で crosstab 再現 | pivot 上限200列 |
| Circle | Scatterplot | 2 measures + optional size | |
| Square | Heatmap | 2 dimensions + 1 measure (color) | |
| Pie | Pie/Donut chart | inner radius 調整で donut | |
| Map | Map (Point/Region) | Point=lat/lon, Region=geo boundary | Tableau 程の高度な geo 未対応 |
| Gantt Bar | Custom Vega-Lite | x/x2 encoding で期間表現 | ネイティブ非対応 |
| Polygon | Custom Vega-Lite | | ネイティブ非対応 |
| Shape | Scatterplot + shape series | | |
| Density | Custom Vega-Lite | | ネイティブ非対応 |

## Omni 固有のビジュアライゼーション (Tableau に無いもの)

- **KPI Cards**: 単一値のハイライト表示
- **Funnel chart**: ファネル分析用
- **Sankey chart**: フロー/遷移の可視化
- **Boxplot**: 分布の要約統計
- **AI Summary**: AI による自動サマリー
- **Markdown tile**: ダッシュボード内のテキスト/Markdown
- **Single record**: 単一レコード表示

## Omni ビジュアライゼーション 全15種 (公式確認済み)

AI Summary, Area, Bar, Boxplot, Funnel, Heatmap, KPI, Line, Map (Point/Region),
Markdown, Pie/Donut, Sankey, Scatterplot, Single Record, Table
+ Custom Vega-Lite / HTML・CSS・Markdown iframe

## Tableau パラメータ -> Omni 代替

| Tableau 機能 | Omni 代替 | 備考 |
|---|---|---|
| パラメータによるメトリクス切替 | **Field Switcher** コントロール | ダッシュボードの Control から設定 |
| Pages シェルフ | ダッシュボード フィルター | Omni には Pages 相当機能なし |
| パラメータによる動的フィルター | Omni Templated Filters | Mustache 構文で参照 |

## Tableau Mark Class 有効値 (公式確認済み)

Automatic, Bar, Line, Area, Square, Circle, Shape, Text, Map, Pie, Gantt Bar, Polygon, Density
