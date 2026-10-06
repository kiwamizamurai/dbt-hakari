<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo.svg" alt="dbt-hakari" width="140">
  </picture>

  # dbt-hakari（秤）

  **BigQuery の請求の式を実データで確かめて、dbt のどのモデルを view から table に（またはその逆に）すべきかを、数理最適化で見極める。**

  ![python](https://img.shields.io/badge/python-3.10%2B-1f3a5f?logo=python&logoColor=white)
  ![license](https://img.shields.io/badge/license-MIT-2a6f4f)
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
uv tool install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.2.0/dbt_hakari-0.2.0-py3-none-any.whl

# 2. dbt プロジェクトで（先に `dbt compile`。target/manifest.json が必要）
hakari collect  --project my-project --location US   # dry-run と、上限つきの履歴 SELECT 1 回
hakari verify                                                      # 請求の式は、実際の請求と合うか
hakari report                                                      # 請求がどこに行っているか
hakari optimize                                                    # どのモデルを table に、または view に戻すか
hakari explain int_orders                                          # 1 つのモデルが、変更に値するか、その理由
```

同じ wheel の URL で、`pipx install` や `pip install` も使えます。インストールせずに試すなら `uvx --from git+https://github.com/kiwamizamurai/dbt-hakari hakari --help` です。BigQuery の権限は、`bigquery.jobs.create` と `bigquery.jobs.listAll` 相当が必要です。コマンド名は `hakari` でも `dbt-hakari` でも同じです。

## 出力の例

```
5 models may change, 12 distinct daily queries
┏━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┓
┃ changes ┃ GiB/day ┃ saving ┃ per month ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━┩
│ <= 0    │ 11.33   │ 0%     │ 0.00 USD  │
│ <= 1    │ 9.42    │ 17%    │ 0.35 USD  │
│ <= 2    │ 6.33    │ 44%    │ 0.92 USD  │
│ <= 4    │ 5.21    │ 54%    │ 1.12 USD  │
└─────────┴─────────┴────────┴───────────┘

today: 11.33 GiB/day = 2.07 USD/month (0.33 TiB; the first 1 TiB/month of the whole project is free)
recommended: 5.21 GiB/day (-54%, saves 1.12 USD/month)
  make a table: int_customers  saves 3.50 GiB/day
  make a table: int_orders  saves 2.32 GiB/day
  make a table: int_order_lines  saves 0.75 GiB/day
  make a table: mart_sales  saves 0.37 GiB/day
```

（合成したプロジェクトの出力例です。）推奨されたモデルを、dbt で `materialized: table` にして、請求で確かめてください。変更する価値がないときは、そう言います。多くのプロジェクトでは、得られるものは小さいです。

> [!WARNING]
> 節約額は**見積もり**です。1 つ変更して、次の数日の請求を比べてから、次の変更に進んでください。

## なぜ必要か

BigQuery のオンデマンド課金は、ほぼ `max(処理バイト, 10 MiB × 参照テーブル数, 10 MiB)` です。dbt の view は、読まれるたびに元のテーブルまで展開されるので、テストや下流のモデルのたびに、これを払い直します。table は、作るときに 1 回払います。モデルをどちらにすべきかは、読むテーブルの数、頻度、結果を読む他の人によって決まります。これは組み合わせ問題で、貪欲法は最適ではありません。変更が、組み合わせて初めて効くことがあるためです。

```
 changes  GiB/day  saving  greedy GiB/day
 <= 1      0.28     0%     0.28 (+0.00)     単独で得になる変更は、ない
 <= 2      0.23    17%     0.28 (+0.05)     でも、この 2 つの組み合わせは得
```

dbt-hakari は、これを厳密に解きます（MILP、HiGHS）。両方向とも解き、解く前に、**あなたのプロジェクトで**式が実際の請求と合うかを確かめます（信頼ゲート）。「秤（はかり）」は、量って比べる道具です。請求を測り、選択肢を比べる、という意味で名付けました。

<details>
<summary><b>各コマンドの役割</b></summary>

- **collect**: 全モデルとテストを dry-run し（無料）、全テーブルのサイズを測り、直近 14 日の `INFORMATION_SCHEMA.JOBS_BY_PROJECT` を **SELECT 1 回**で読みます。課金されるのはこれだけです（約 0.6 GiB。月 1 TiB の無料枠の中で、上限は 1 GiB。超える場合は、課金されずにクエリが失敗します）。`--no-history` で省けます。結果は `.hakari/` に保存します。これ以外のクエリは、BigQuery に送りません。
- **verify**: dbt のグラフから数えたテーブル数が dry-run の参照テーブルと一致するか、式が実際の請求と合うかを確かめます（直近 `--recent-days`（既定 3）日の最大の請求と比べます）。PASS、WARN、FAIL（終了コード 4）。履歴の期間中に変更されたと思われるモデル（昔の実行が、まだ履歴に残っています）も、指摘します。
- **report**: 請求がどこに行っているか。今の設定での、1 日のクエリを、種類（テスト、構築、モデル、dbt 以外のクエリ）別にまとめて、大きい順に並べ、月額を出します。`--top N`。
- **optimize**: 変更が最大 K = 0, 1, 2, … 個のときの最適な選び方を解き、最良に 2% 以内で届く、最小の K を推奨します。組み合わせで効く変更も見つかります。結果が、お金でいくらか、無料枠と比べてどうかも、示します。
- **explain** `<モデル>`: 1 つのモデルが、変更に値するか、その理由。何が読んでいて、変更の前後で、それぞれがいくら払うか、節約額、そのままにする理由、直近の `optimize` が推奨したか。
- **users**: 履歴の中で、誰がジョブを実行したか。

費用を継続的に監視するのは、[dbt-bigquery-monitoring](https://github.com/bqbooster/dbt-bigquery-monitoring) のようなパッケージの役目です。dbt-hakari は、別の問い、「何を変えるべきか、いくら減るか」に答えます。

</details>

<details>
<summary><b><code>optimize</code> のオプション</b></summary>

| オプション | 意味 |
|---|---|
| `--max-k N` | 変更は最大 N 個（既定 8） |
| `--direction views\|tables\|both` | 推奨してよい変更（既定は both） |
| `--exclude SELECTOR` | そのままにするモデル: `tag:x`、`path:models/a/*`、`uid:model.pkg.*`、`name:int_*`、`package:pkg`、モデル名の glob。複数指定可。接頭辞の誤字は、エラーになります |
| `--force-table UID`、`--force-view UID` | table にする、または view のままにする、候補のモデル |
| `--assume-daily` | 履歴を無視して、全クエリを 1 日 1 回として数える |
| `--compare-greedy` | 貪欲法が届く値の列を足す |
| `--time-limit S` | 1 回の求解の秒数（既定 60）。それまでに解がなければ、終了コード 6 |
| `--allow-unverified` | 信頼ゲートが FAIL でも、先に進む |
| `--price-per-tib X` | オンデマンドの単価（既定 6.25） |
| `--output-ratio R` | view の table のサイズ。クエリがスキャンするバイト数に対する比（既定 1.0） |
| `--only-user`、`--exclude-user`、`--all-users` | 誰のジョブを数えるか |
| `--format text\|json\|markdown` | 出力の形式 |

多くは、`hakari.toml` でも指定できます（下記）。

</details>

<details>
<summary><b>両方向と、table を他に誰が読んでいるか</b></summary>

`--direction views|tables|both`（既定は both）。読み手が何度も払い直している view は table に、作るのが安く、読む人が少ない table は view に戻します。

table を view にするのは、**読む人が全員分かっているとき**だけです。dbt の読み手に加えて、アプリや BI のサービスが、その table に実行したクエリを、`collect` が履歴から読みます。exposure が読む table や、読み手が不明な table は、そのままにします。view は、読まれるたびに、SQL がスキャンする分を計算し直すと仮定するので、作るのが高い table は、table のままです。

view を table にしたときの大きさは、作る前には分かりません。`--output-ratio`（既定は 1.0 で、バイトの削減は見込みません）や、設定の `[sizes]` で、分かっているサイズを与えられます。

</details>

<details>
<summary><b>ローカルの実行、スケジュール、<code>--all-users</code></b></summary>

ローカルからの実行や、アナリストのアドホックなクエリは、スケジュールではありません。サービスアカウントと人の両方がジョブを実行しているときは、既定でサービスアカウントが実行した分（dbt のジョブと、アプリや BI サービスのクエリ）だけを数え、そう表示します。`hakari users` で、誰が何を実行したか分かります。`--only-user` と `--exclude-user`（アドレスや、`*@example.com` のような glob）で自分で選ぶか、`--all-users` で全員を数えます。

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
<summary><b>そのままにするモデルを固定する（<code>.hakari-ignore</code>）</b></summary>

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
location = "US"
price_per_tib = 6.25
lookback_days = 14

[optimize]
max_k = 8
direction = "both"
output_ratio = 1.0
exclude = ["path:models/adhoc/*"]
exclude_users = ["*@example.com"]

[sizes]
int_orders = 120000000   # table のバイト数。分かっていれば
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
- incremental、snapshot、seed は、固定として扱います。変更の推奨はせず、費用は dry-run から見積もるので、incremental の 1 回の実行は、多めに出ることがあります。
- 見積もりは、BigQuery での統制した実験と、実プロジェクト 1 つで確かめたもので、多くのプロジェクトでは確かめていません。
- 月 1 TiB まではプロジェクト全体で無料なので、節約がお金になるのは、それを超えるときだけです。

</details>

## 開発

```bash
uv sync --extra dev
uv run pytest && uv run ruff check src tests && uv run mypy
```

[CONTRIBUTING.md](CONTRIBUTING.md) を見てください。[MIT](LICENSE)。
