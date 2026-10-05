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

BigQuery（オンデマンド課金）と dbt のプロジェクトで、**どの view を table にすれば日次の請求が最小になるか**を、整数計画法で厳密に求めるツールです。最適化の前に、請求の式がそのプロジェクトで本当に成り立つかを、実際の請求履歴と突き合わせて確かめます（信頼ゲート）。

状態: alpha。ライセンスは MIT です。名前の「秤（はかり）」は、量って比べる道具から取りました。請求を測り、選択肢を比べる、という意味です。

> [!NOTE]
> dbt-hakari は **PyPI には公開していません**。[GitHub Release](https://github.com/kiwamizamurai/dbt-hakari/releases/latest) の wheel を入れてください（[インストール](#インストール)を参照）。`pip install dbt-hakari` では見つかりません。

## なぜ必要か

オンデマンド課金の請求額は、ほぼ次の式で決まります。

```
請求バイト = max(処理バイト, 10 MiB × 参照テーブル数, 10 MiB)
```

dbt の view は参照されるたびに元のテーブルまで展開されます。view に付けたテストや、下流のモデルの数だけ、同じテーブル群への最小課金（10 MiB × テーブル数）が重なります。たとえば 3 つのテーブルを束ねる view を 2 本のテストが読むと、table 化したほうが得になります（一般には、T テーブルを n 人が読むとき n·T > T + n）。

どの view を table にするかは組み合わせ問題で、貪欲法では最適になりません。最適解は、選ぶ数 K を増やしても入れ子になりません。このツールは MILP（HiGHS）で厳密に解きます。

## インストール

PyPI には公開していません。[最新の GitHub Release](https://github.com/kiwamizamurai/dbt-hakari/releases/latest) の wheel を入れます。

```bash
uv tool install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.1.0/dbt_hakari-0.1.0-py3-none-any.whl
# または
pipx install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.1.0/dbt_hakari-0.1.0-py3-none-any.whl
# または、仮想環境の中で
pip install https://github.com/kiwamizamurai/dbt-hakari/releases/download/v0.1.0/dbt_hakari-0.1.0-py3-none-any.whl
```

インストールせずに試す場合や、未リリースの `main` を使う場合は、次のようにします。

```bash
uvx --from git+https://github.com/kiwamizamurai/dbt-hakari hakari --help
```

Python 3.10 以上。`dbt-core` には依存せず、`manifest.json` を JSON として読みます。コマンド名は `hakari` でも `dbt-hakari` でも同じです。開発については、[CONTRIBUTING.md](CONTRIBUTING.md) を見てください。

## 使い方

前提: `dbt compile` で `target/manifest.json` ができていて（`dbt parse` だけでは、コンパイル済みの SQL がないので足りません。dbt-core 1.5 以上、manifest のスキーマ v9 以上）、BigQuery の `bigquery.jobs.create` と `bigquery.jobs.listAll` 相当の権限があること。

### 1. collect: 材料を集める

```bash
dbt-hakari collect --manifest target/manifest.json \
  --project my-project --location asia-northeast2
```

- 全モデルとテストを **dry-run** して、処理バイトと参照テーブルを調べます（無料）。
- 直近 14 日の `INFORMATION_SCHEMA.JOBS_BY_PROJECT` を **1 回だけ** SELECT して、dbt が実行したクエリの実際の請求を集めます。課金される処理はこれだけです。14 日分で約 0.5 GiB 読みます（月 1 TiB の無料枠の中で、約 0.6 円相当）。`maximum_bytes_billed`（既定 1 GiB）で上限を付けてあり、超えるとクエリは失敗して課金されません。履歴が要らなければ `--no-history` を付けます。
- 結果は `.hakari/`（`--data-dir` で変更可）に保存されます。

> [!TIP]
> 課金されるのは、履歴の読み取りだけです（14 日分で約 0.5 GiB。月 1 TiB の無料枠の中）。上限は 1 GiB に設定してあります。`--no-history` を付けると、履歴を読まず、dry-run だけで進めます。

BigQuery に発行するのは、dry-run と、この INFORMATION_SCHEMA への SELECT だけです。それ以外を発行しようとすると、ツールが例外で止めます。

### 2. verify: 請求の式を信頼してよいか確かめる

```bash
dbt-hakari verify --manifest target/manifest.json
```

```
trust gate: PASS
 graph == dry-run table counts  100.0% of 32 nodes
 bill within 5% of the formula  100.0% of 32 nodes
 median |error|                 0.00%
 formula too high / too low     0.6% / 2.2%
 nodes left out                 26
```

- 層 1: dbt のグラフから数えたテーブル数が、dry-run の参照テーブル数と一致するか。ずれたノード（INFORMATION_SCHEMA を直接読む view など）は除外して、理由を出します。
- 層 2: `max(処理バイト, 10 MiB × テーブル数)` が、実際の請求と合うか。比べる相手は、直近 `--recent-days`（既定 3）日の最大の請求です。`dbt run --empty` のような部分実行は、最小課金しか発生せず、実行の実態を表さないためです。
- 合格は PASS、一部だけ怪しいときは WARN、不合格は FAIL です。予約（スロット）課金のプロジェクトは、このツールの金額が意味を持たないので FAIL にします。

### 3. optimize: 最適な view を求める

```bash
dbt-hakari optimize --manifest target/manifest.json --max-k 8 --compare-greedy
```

```
 K  GiB/day  saving  per month   greedy GiB/day
 0   0.52     0%     0.00 USD    -
 1   0.44    17%     0.02 USD    0.44 (+0.00)
 3   0.36    32%     0.03 USD    0.37 (+0.01)
 5   16.16    34%     1.55 USD    16.63 (+0.47)
 8   0.36    32%     0.03 USD    0.38 (+0.02)
unconstrained optimum: 0.36 GiB/day (32% saving) with 2 views

recommended: materialize 2 view(s)
  int_orders  saves 0.09 GiB/day
  ...
```

- K（table にする view の数）を 0 から増やして、それぞれの最小値を出します。限界利得が小さくなる K を推奨します。
- `--compare-greedy` で、貪欲法との差を並べます。差が出る行は、最適解が入れ子でないことの現れです。
- `--assume-daily` は、全クエリを 1 日 1 回として数えます。付けなければ、履歴から推定した実行頻度を使います。
- 候補から外す: `--exclude 'tag:x'`、`--exclude 'path:models/adhoc/*'`、`--exclude 'name*'`。必ず table にする、必ず view のままにする: `--force-table`、`--force-view`。
- verify が FAIL のときは、終了コード 4 で拒否します。理解したうえで進めるときは `--allow-unverified` を付けます。

## CI で使う

`verify` と `optimize` は `--format json|markdown` を受け取ります。結果は stdout だけに出て、メッセージは stderr に出るので、そのままパイプや投稿に使えます。

```bash
dbt-hakari verify --format json > verification.json           # 信頼ゲートが FAIL なら終了コード 4
dbt-hakari optimize --format markdown >> "$GITHUB_STEP_SUMMARY"
```

終了コードは下の表のとおりです。`verify --strict` は WARN でも 10 で終了します。ゲートが FAIL のときも、`optimize` は終了コード 4 の前に、指定の形式で検証結果を出します。

## view のまま残したいものを固定する（`.hakari-ignore`）

1 行に 1 つ、`--exclude` と同じ書き方で書きます。` #` のあとは理由で、プランに表示されます。

```
int_orders          # 外部の BI ツールが読んでいる
tag:keep_view
path:models/adhoc/*
```

別のファイルは `--ignore-file` で指定します。

## 設定ファイル（hakari.toml または pyproject.toml）

```toml
[bigquery]
project = "my-project"
location = "asia-northeast2"
price_per_tib = 6.25
currency = "USD"
lookback_days = 14

[optimize]
max_k = 8
exclude = ["path:models/adhoc/*"]
```

設定は、`--config`、なければ `hakari.toml`、なければ `pyproject.toml` の `[tool.dbt-hakari]` の順に読みます（キーは同じで、`[tool.dbt-hakari.optimize]` のように書きます）。

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 2 | 使い方の誤り |
| 3 | manifest を読めない |
| 4 | 信頼ゲートが FAIL |
| 5 | BigQuery 側のエラー |
| 6 | ソルバが時間切れ（最良の実行可能解を返す） |
| 10 | `--strict` で WARN |

## 限界と注意

> [!WARNING]
> 節約額は**見積もり**です。view を変更したあとは、請求で確かめてください。

- 「table にしても、読み手の処理バイトは変わらない」と仮定しています（述語のプッシュダウンが効いていた view は、table 化でスキャンが増えることもあります）。
- 対象は BigQuery のオンデマンド課金と dbt です。Editions や予約課金、BI Engine は対象外です（検出して警告します）。
- 公式ドキュメントでは、最小課金の単位は MiB（参照テーブル 1 つにつき 10 MiB、クエリ全体で 10 MiB）です。
- wildcard テーブルの最小課金の挙動は検証していません。

## 開発

```bash
.venv/bin/python -m pytest      # 単体テストと、全探索との照合（hypothesis）
.venv/bin/ruff check src tests
.venv/bin/mypy
```

## 仕組み（要約）

選ぶ view を 2 値変数 $x_v$ にし、各クエリ $n$ が読むテーブルの集合への到達可能性 $r(n,u)$ を不等式で表し、請求 $c_n \ge 10\,\mathrm{MiB}\cdot T_n$ かつ $c_n \ge B_n$ を最小化します。table 化した view の構築コストも、$x_v$ で有効になる形で加えます。$\sum x_v \le K$ の制約のもとで、`scipy.optimize.milp`（HiGHS）で解きます。
