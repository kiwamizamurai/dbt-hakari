<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo.svg" alt="dbt-hakari" width="140">
  </picture>

  # dbt-hakari（秤）

  **BigQuery の請求の式を実データで確かめて、table にすべき dbt の view を、数理最適化で見極める。**

  ![python](https://img.shields.io/badge/python-3.10%2B-1f3a5f?logo=python&logoColor=white)
  ![status](https://img.shields.io/badge/status-開発中-c8372d)
  ![solver](https://img.shields.io/badge/solver-HiGHS%20(MILP)-3c5f8f)
  ![mypy](https://img.shields.io/badge/mypy-strict-2a6f4f)
  ![ruff](https://img.shields.io/badge/lint-ruff-d7ff64?labelColor=1f3a5f)
  ![bigquery](https://img.shields.io/badge/BigQuery-on--demand-4285f4?logo=googlebigquery&logoColor=white)

  [English](README.md) | **日本語**
</div>

---

> [!NOTE]
> dbt-hakari は **PyPI には公開していません**。[最新の GitHub Release](https://github.com/kiwamizamurai/dbt-hakari/releases/latest) の wheel を入れてください。`pip install dbt-hakari` では見つかりません。

## クイックスタート

```bash
# 1. インストール（Python 3.10 以上）
uv tool install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.1.0/dbt_hakari-0.1.0-py3-none-any.whl

# 2. dbt プロジェクトで（先に `dbt compile`。target/manifest.json が必要）
hakari collect  --project my-project --location asia-northeast2   # dry-run と、上限つきの履歴 SELECT 1 回
hakari verify                                                      # 請求の式は、実際の請求と合うか
hakari optimize --max-k 8 --compare-greedy                         # どの view を table にするか
```

同じ wheel の URL で、`pipx install` や `pip install` も使えます。インストールせずに試すなら `uvx --from git+https://github.com/kiwamizamurai/dbt-hakari hakari --help` です。BigQuery の権限は、`bigquery.jobs.create` と `bigquery.jobs.listAll` 相当が必要です。コマンド名は `hakari` でも `dbt-hakari` でも同じです。

## 出力の例

```
trust gate: PASS
 bill within 5% of the formula  100.0% of 32 nodes

 K  GiB/day  saving  greedy GiB/day
 0   0.52     0%     -
 1   0.44    17%     0.44 (+0.00)
 2   0.36    32%     0.36 (+0.00)
 4   0.36    32%     0.38 (+0.02)

recommended: materialize 2 view(s)
  int_orders        saves 0.09 GiB/day
  int_order_lines   saves 0.08 GiB/day
```

（合成したプロジェクトの出力例です。）推奨された view を、dbt で `materialized: table` にして、請求で確かめてください。

> [!WARNING]
> 節約額は**見積もり**です。「view を table にしても、読み手の処理バイトは変わらない」と仮定しています。view を変更したあとは、請求で確かめてください。

## なぜ必要か

BigQuery のオンデマンド課金は、ほぼ `max(処理バイト, 10 MiB × 参照テーブル数, 10 MiB)` です。dbt の view は、読まれるたびに元のテーブルまで展開されるので、テストや下流のモデルのたびに、テーブルごとの最小額を払い直します。T 個のテーブルを n 個が読む view は、n·T > T + n のとき、table にすると得です。どの view を table にするかは組み合わせ問題で、貪欲法は最適ではなく、K 個の最適解は、K+1 個の最適解に含まれません。dbt-hakari は、これを厳密に解きます（MILP、HiGHS）。解く前に、**あなたのプロジェクトで**式が実際の請求と合うかを確かめます（信頼ゲート）。

「秤（はかり）」は、量って比べる道具です。請求を測り、選択肢を比べる、という意味で名付けました。

<details>
<summary><b>各コマンドの役割</b></summary>

- **collect**: 全モデルとテストを dry-run し（無料）、直近 14 日の `INFORMATION_SCHEMA.JOBS_BY_PROJECT` を **SELECT 1 回**で読みます。課金されるのはこれだけです（約 0.5 GiB。月 1 TiB の無料枠の中で、上限は 1 GiB。超える場合は、課金されずにクエリが失敗します）。`--no-history` で省けます。結果は `.hakari/` に保存します。これ以外のクエリは、BigQuery に送りません。
- **verify**: dbt のグラフから数えたテーブル数が dry-run の参照テーブルと一致するか、式が実際の請求と合うかを確かめます（直近 `--recent-days`（既定 3）日の最大の請求と比べるので、`dbt run --empty` に惑わされません）。PASS、WARN、FAIL（終了コード 4）。予約（スロット）課金は FAIL です。
- **optimize**: K = 0, 1, 2, … 個の最適な選び方を解き、1 個増やしても得にならなくなる K を推奨します。`--assume-daily` は全クエリを 1 日 1 回として数えます。`--exclude 'tag:x'`、`--force-table`、`--force-view` で絞り込み、`--allow-unverified` でゲートの失敗を押し切れます。

</details>

<details>
<summary><b>CI で使う</b></summary>

`verify` と `optimize` は `--format json|markdown` を受け取ります。結果は stdout だけに、メッセージは stderr に出ます。

```bash
hakari verify --format json > verification.json              # ゲートが FAIL なら終了コード 4
hakari optimize --format markdown >> "$GITHUB_STEP_SUMMARY"
```

`verify --strict` は WARN でも 10 で終了します。ゲートが FAIL のときも、`optimize` は終了コード 4 の前に検証結果を出します。

</details>

<details>
<summary><b>view のまま残すものを固定する（<code>.hakari-ignore</code>）</b></summary>

1 行に 1 つ、`--exclude` と同じ書き方で書きます。` #` のあとは理由で、プランに表示されます。別のファイルは `--ignore-file` で指定します。

```
int_orders          # 外部の BI ツールが読んでいる
tag:keep_view
path:models/adhoc/*
```

</details>

<details>
<summary><b>設定ファイルと終了コード</b></summary>

設定は、`--config`、なければ `hakari.toml`、なければ `pyproject.toml` の `[tool.dbt-hakari]` の順に読みます。誤った値は、項目名つきで報告されます。

```toml
[bigquery]
project = "my-project"
location = "asia-northeast2"
price_per_tib = 6.25
lookback_days = 14

[optimize]
max_k = 8
exclude = ["path:models/adhoc/*"]
```

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 2 | 使い方の誤り |
| 3 | manifest を読めない |
| 4 | 信頼ゲートが FAIL |
| 5 | BigQuery 側のエラー |
| 6 | ソルバが時間切れ（最良の実行可能解を返す） |
| 10 | `--strict` で WARN |

</details>

<details>
<summary><b>限界</b></summary>

- 対象は BigQuery のオンデマンド課金と dbt です。Editions、予約課金、BI Engine は対象外です（検出して警告します）。コンパイル済み SQL を含む、dbt-core 1.5 以上の manifest（スキーマ v9 以上）が必要です。
- 最小課金は、参照テーブルごとに 10 MiB、クエリごとに 10 MiB です（公式ドキュメント）。wildcard テーブルの最小課金は未検証です。
- alpha: 実プロジェクト 1 つでしか検証していません。

</details>

## 開発

```bash
uv sync --extra dev
uv run pytest && uv run ruff check src tests && uv run mypy
```

[CONTRIBUTING.md](CONTRIBUTING.md) を見てください。[MIT](LICENSE)。
