# M1風採点アプリ

発表会やコンテストの採点を、M1グランプリ風の結果発表演出つきで行うWebアプリです。
以前は採点入力アプリ(saiten)と結果発表アプリ(result)に分かれていましたが、
現在は1つのFlaskアプリに統合されています。

## アプリ概要

1つのアプリで、次の流れを完結できます。

- **プロジェクト作成** — 被採点者(チーム)、採点者、採点軸5個を登録してプロジェクトを作る
- **Host** — ホストコードでログインし、進捗確認・採点締切・結果発表を操作する
- **Scorer** — 参加コードでログインし、担当する被採点者を採点する
- **Scoring** — 採点軸ごとの点数と自由記述フィードバックを入力(自動保存あり)、確定して提出
- **Result Presentation** — 被採点者ごとに点数を順番に開示し、最後に最終ランキングを表示

### 画面

| 画面 | URL |
|---|---|
| ホーム | `/` |
| プロジェクト作成 | `/projects/new` |
| ホストログイン | `/host/login` |
| 採点者として参加 | `/join` |
| Host Dashboard | `/host/<project_id>` |
| Host Settings | `/host/<project_id>/settings` |
| 結果発表 | `/host/<project_id>/present` |
| 結果分析 | `/host/<project_id>/analysis` |
| Scorer Dashboard | `/scorer` |
| 採点 | `/scorer/subjects/<subject_id>` |

### プロジェクトの状態遷移

```text
DRAFT → SCORING → LOCKED → PRESENTING → FINISHED
```

一方向のみで、巻き戻しはできません。**結果発表方式にかかわらず同じです。**

- `DRAFT` — 被採点者・採点者・採点軸名を編集できる唯一の状態
- `SCORING` — 採点者が採点・提出できる。構成変更は禁止
- `LOCKED` — 採点締切。採点者からの書き込みは全て拒否される
- `PRESENTING` — 結果発表中
- `FINISHED` — 発表完了(ホストは結果を再閲覧できる)

### 結果発表方式

プロジェクト作成時に2つの方式から選べます。既存プロジェクトと、方式を指定せずに
作成したプロジェクトはすべて `BATCH` です。

| 方式 | 内容 |
|---|---|
| `BATCH`(全発表者終了後にまとめて発表) | 従来どおり。全員の採点が終わってから、全被採点者の結果をまとめて発表します |
| `SEQUENTIAL`(発表者ごとに採点・発表) | M-1方式。1組ずつ「採点 → 締切 → 得点発表」を繰り返し、最後に最終ランキングを出します |

`SEQUENTIAL` では、被採点者ごとに次の状態を持ちます(こちらも一方向のみ)。

```text
WAITING → SCORING → LOCKED → PRESENTED
```

- 採点できるのは `SCORING` の被採点者ただ1人だけです。先行採点はできません。
- 全被採点者が `PRESENTED` になって初めて、プロジェクトを `LOCKED` にできます。
- 被採点者の発表中、プロジェクトの状態は `SCORING` のまま動きません。
  `SCORING ↔ PRESENTING` のような往復は発生しません。

### 集計ルール

- 同点は competition ranking(1位, 1位, 3位)で表示します。
- どちらの方式でも、**全被採点者が同じ人数の採点者によって採点されている**状態を保ちます。
  これが崩れると被採点者間で合計点を比較できなくなるためです。方式ごとに守り方が違います。

**BATCH の場合**

- 公式集計の対象は **eligible scorer** のみです。eligible scorerとは
  「そのプロジェクトの全被採点者に対して提出を完了した採点者」を指します。
- 一部の被採点者しか提出していない採点者のデータは、提出済みの分も含めて
  集計から除外されます(DBからは削除されません)。
- eligible scorerが0人の場合は締切できません。
- 未完了の採点者がいる状態でも、ホストは強制的に締切できます
  (除外される人数が画面に表示されます)。

**SEQUENTIAL の場合**

- 公式集計の対象は参加採点者全員です。
- **強制締切はできません。** 被採点者を締め切るには、参加採点者全員の提出が必要です。
- **採点者が参加できなくなると、その被採点者を締め切れず進行が止まります。**
  逐次発表方式を選ぶ場合は、当日確実に参加できる採点者だけを登録してください。
- 参加できなくなった採点者の代わりに、ホストが他人名義で代理提出することは
  想定していません(誰がどう採点したかという記録が壊れるため)。
  参加コードの再発行は、あくまで**本人がコードを紛失した場合の復旧手段**です。

### 結果の確認と保存

採点締切(`LOCKED`)以降、ホストは結果分析画面を利用できます。

- 被採点者ごとの公式スコア、採点軸別平均(レーダーチャート)、採点者別合計
- **提出済みフィードバックの一覧。** BATCHの強制締切で公式集計から除外された
  採点者のフィードバックも、「公式集計対象外」と明示したうえで閲覧できます
  (点数は集計に加算されません)。未提出(下書き)のものは含みません。
- CSV(Excelでそのまま開けるUTF-8 BOM付き)と Markdown でのダウンロード

ホストコード・参加コードおよびそのハッシュは、いかなる出力にも含まれません。

発表完了(`FINISHED`)後も、発表演出の再生・最終ランキングの再表示・結果分析が
できます。再生は表示だけの操作で、プロジェクトの状態は `FINISHED` のまま変わりません。

## ローカル開発

### 必要環境

- Python **3.14.4**(リポジトリルートの `.python-version` で固定)

Renderは `.python-version` を読み取ってランタイムのPythonバージョンを決定します。
ローカルと本番でバージョンを揃えるため、このファイルは削除しないでください。

### セットアップ

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # 本番依存 + pytest
```

本番相当の依存だけを入れる場合は `requirements.txt` を使います。

### 環境変数

| 変数 | 必須 | 説明 |
|---|---|---|
| `APP_ENV` | 必須 | `development` / `testing` / `production` のいずれか。未設定・不正値は起動失敗 |
| `SECRET_KEY` | 必須 | セッション署名用。未設定は起動失敗 |
| `DATABASE_URL` | production では必須 | 本番のPostgreSQL接続文字列。development では未設定ならローカルSQLite |
| `MIGRATION_DATABASE_URL` | 任意 | migration専用の接続文字列。未設定時は `DATABASE_URL` を使う |

`APP_ENV` にデフォルト値はありません。設定を忘れたまま本番が
ローカルSQLiteで動いてしまう事故を防ぐためです。

### 起動

```bash
export FLASK_APP=wsgi.py
export APP_ENV=development
export SECRET_KEY=<ローカル用の任意の文字列>

.venv/bin/flask db upgrade          # data/dev.sqlite3 にスキーマを作成
.venv/bin/flask run --port 8765     # 開発サーバー
```

本番と同じ構成で確認したい場合:

```bash
.venv/bin/gunicorn wsgi:app --bind 0.0.0.0:8765 --workers 1
```

エントリポイントは `wsgi.py` の `app` です(App Factory `create_app()` を呼びます)。

### テスト

```bash
.venv/bin/python -m pytest tests/
```

テストはSQLiteのインメモリDBを使い、外部サービスには一切接続しません。

### DBマイグレーション

```bash
.venv/bin/flask db upgrade    # 適用
.venv/bin/flask db check      # モデルとスキーマの差分確認
```

マイグレーションは2本あります。

| revision | 内容 |
|---|---|
| `b37d61517847` | 初期スキーマ。**本番(Neon)へ適用済みのため、絶対に書き換えないでください。** |
| `9c4e17a2b8d3` | 結果発表方式(`projects.presentation_mode`)と被採点者の進行状態(`subjects.presentation_status` ほか)を追加 |

2本目は `ADD COLUMN` のみの expand-only マイグレーションです。既存行への `UPDATE` は
発行せず、`server_default` によって既存プロジェクトが `BATCH` として扱われます。
CHECK制約はカラム定義に含める形で付けており、SQLiteでもテーブル再構築なしに
PostgreSQLと同じ制約が入ります。

このスキーマ変更は**旧バージョンのアプリと後方互換**です。追加した列にはすべて
デフォルトがあり、Phase 8以前のコードはこれらを参照しません。そのため本番で
問題が起きた場合の切り戻しは、Neonのスキーマをそのままにして
**Renderで以前のコミットを再デプロイするだけ**で済みます。

`data/dev.sqlite3` は開発専用でgit管理対象外です。作り直したい場合は
削除して `flask db upgrade` を実行してください。

## Production方針

**Phase 7で設定予定です。まだ本番環境は構築していません。**

- **Web**: Render Free Web Service
- **DB**: Neon Free PostgreSQL
- **本番でSQLiteは使用しません。** Render Free Web Serviceのファイルシステムは
  再起動・再デプロイで消えるため、永続データは必ずNeonに保存します。
- `APP_ENV=production` が必須です。
- `DATABASE_URL` が必須です。未設定の場合、SQLiteへフォールバックせず起動に失敗します。
- `MIGRATION_DATABASE_URL` には Neon の direct connection を指定します
  (アプリ実行時の `DATABASE_URL` には pooled connection を指定します)。
- マイグレーションはWebサーバー起動時に自動実行しません。デプロイのbuild段階で
  適用します。破壊的なスキーマ変更は expand/contract 方式で分割してください。

### Render 設定値

| 項目 | 値 |
|---|---|
| Build Command | `pip install -r requirements.txt && flask --app wsgi:app db upgrade` |
| Start Command | `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1` |
| Health Check Path | `/healthz` |
| Python Version | `.python-version`(3.14.4)で自動決定 |

環境変数(値はRender Dashboardへ直接入力し、リポジトリには保存しません):

| 変数 | 値の種類 |
|---|---|
| `APP_ENV` | `production` |
| `SECRET_KEY` | 十分に長いランダム文字列 |
| `DATABASE_URL` | Neon **pooled** connection(ホスト名に `-pooler` を含む) |
| `MIGRATION_DATABASE_URL` | Neon **direct** connection(`-pooler` を含まない) |

Render Free Web Service では Pre-Deploy Command が使えないため、
マイグレーションは Build Command 内で実行します。

### 安全なデプロイ手順(事故防止)

既存のRender Web Serviceを再利用するため、Auto-Deployが有効なまま
ブランチをmainへマージすると意図せず本番デプロイが発火します。
本番切替は必ず次の順序で行ってください。

1. Render の **Auto-Deploy を一時的に Off** にする
2. Neon Free プロジェクトを作成し、pooled / direct 両方の接続文字列を取得する
3. Render の環境変数・Build Command・Start Command・Health Check Path を設定する
4. `feature/unified-scoring-app` で検証済みのコミットを本番候補として扱う
5. 手動デプロイを実行する
6. production smoke test を実施する
7. 問題がなければ main への統合方針を決める
8. 最後に Auto-Deploy を再設定する

**feature ブランチをいきなり main へマージして Auto-Deploy を発火させないでください。**

新統合アプリの smoke test が完全に成功するまで、旧 result アプリの
Render Service は削除・停止・設定変更しないでください。

## 運用注意

- **コールドスタート**: Render Free Web Service と Neon Free はどちらも
  アイドル状態で停止します。復帰に時間がかかるため、発表会などの前には
  一度アクセスしてウォームアップしてください。
- **プロジェクトを作成したブラウザは、そのままホストになります**: 作成完了時点で
  ホストとしてログイン済みになるため、ホストコードの再入力は不要です。
  別の端末・別のブラウザ・後日のログインにはホストコードが必要です。
  なお、セッションが保持できるホスト権限は常に1プロジェクト分だけです。
- **逐次発表方式では全採点者の提出が必要です**: 採点者が参加できなくなると
  その被採点者を締め切れず、進行が止まります(強制締切はありません)。
- **ホストコードを失うと復旧できません**: ホストコードはDBにハッシュのみ保存され、
  平文は作成直後・再発行直後にしか表示されません。ホストコードを紛失し、
  かつホストとしてのログイン状態も失われた場合、このMVPではそのプロジェクトへ
  アクセスする手段がなくなります。
- **参加コードも再表示できません**: 同じ理由で、発行済みの参加コードを
  後から一覧表示することはできません。紛失した場合はHost Settingsから
  再発行してください。
- **再発行すると旧コードは無効になります**: ホストコード・参加コードのいずれも、
  再発行した時点で以前のコードは使えなくなります。
- **ログイン試行回数制限**: ホスト/参加コードの認証にはレート制限をかけています。
  1インスタンス構成前提のベストエフォート実装です。
