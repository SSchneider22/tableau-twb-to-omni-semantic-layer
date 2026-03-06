# Tableau TWB -> Omni Semantic Layer + Chart Reproduction Migrator

Tableau の `.twb`（XML workbook）から、Omni の Semantic Layer YAML とチャート再現用 Markdown ガイドを自動生成する [Claude Code](https://docs.anthropic.com/en/docs/claude-code) Skill です。

## Generated Output

| Output | Description |
|---|---|
| `views/*.yaml` | Omni View 定義 |
| `relationships.yml` | View 間の join 定義 |
| `topics/*.topic` | Tableau Datasource ごとに 1 つの Topic |
| `charts/index.md` | 全体概要 + チャートタイプ対応表 + ダッシュボード構成 |
| `charts/<sheet_name>.md` | シートごとの Omni 再現手順 |

## Claude Code Skill としての使い方

Claude Code 上で `.twb` ファイルを渡して変換を依頼するだけで利用できます。

```
この .twb ファイルを Omni セマンティックレイヤーに変換してください → /path/to/workbook.twb
```

Skill が自動的に `.twb` を解析し、Omni の View / Relationship / Topic YAML とチャート再現用 Markdown を生成します。

## Prerequisites（スクリプト直接実行時）

- Python 3.10+
- lxml / PyYAML

## Usage（スクリプト直接実行）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt

python scripts/twb_to_omni.py \
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
| `--default-schema` | Schema name when TWB omits it | `PUBLIC` |
| `--topic-group-label` | `group_label` for all generated topics | (none) |
| `--max-topic-join-depth` | Max depth of joins tree per topic | `4` |

## Post-Generation Checklist

### Semantic Layer YAML

- **relationships.yml** - `on_sql` が TODO になっていないか / `relationship_type` を正しく設定したか
- **topics/*.topic** - `base_view` が妥当か / joins が出しすぎになっていないか
- **LOD** - `level_of_detail` の fixed/always_include/always_exclude が想定通りか
- **Parameters** - filter-only fields の型が妥当か
- 必要に応じて primary key の定義や distinct-on 集計の設計を追加する

### Chart Reproduction Markdown

- `charts/index.md` のチャートタイプ対応が正しいか
- 各シート Markdown のフィールドマッピングが Omni フィールド名と一致しているか
- Custom Vega-Lite が必要なチャートを特定し、対応方針を決めたか
- ダッシュボード構成（含まれるシート）が正しいか

## Post-Processing Rules

- **`aggregate_type: number` は使わない** - `number` は Omni の無効値。sql に集計関数を直接含む measure は `aggregate_type` 自体を省略する
- **Topic joins に `join_via` は存在しない** - 間接 join は必ずネスト構造で表現する
- **Topic joins のネストは直接 relationship 経路に従う**
- **`sample_queries` の `topic:` は `group_label`（日本語 Topic 名）を使用する**
- **Tableau LOD 計算 -> Omni `level_of_detail` dimension** - 必ず `dimensions` セクションに配置する
- **`allowed_values` / `default` は使用不可** - `suggestion_list` / `default_filter` を使用する
- **パラメータ参照は Mustache 構文**: `{{filters.<view>.<filter>.value}}`
- **Tableau データソースフィルター（相対日付）-> Omni `default_filters`**: `time_for_duration` に変換

## References

- [references/omni-yaml-primer.md](references/omni-yaml-primer.md) - Omni YAML 構文リファレンス
- [references/object-mapping.md](references/object-mapping.md) - Tableau -> Omni オブジェクト対応表
- [references/limitations.md](references/limitations.md) - 制限事項
- [references/chart-type-mapping.md](references/chart-type-mapping.md) - チャートタイプ対応表
