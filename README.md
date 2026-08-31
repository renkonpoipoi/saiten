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
| Scorer Dashboard | `/scorer` |
| 採点 | `/scorer/subjects/<subject_id>` |

### プロジェクトの状態遷移

```text
DRAFT → SCORING → LOCKED → PRESENTING → FINISHED
```

一方向のみで、巻き戻しはできません。

- `DRAFT` — 被採点者・採点者・採点軸名を編集できる唯一の状態
- `SCORING` — 採点者が採点・提出できる。構成変更は禁止
- `LOCKED` — 採点締切。採点者からの書き込みは全て拒否される
- `PRESENTING` — 結果発表中
- `FINISHED` — 発表完了(ホストは結果を再閲覧できる)

### 集計ルール

- 公式集計の対象は **eligible scorer** のみです。eligible scorerとは
  「そのプロジェクトの全被採点者に対して提出を完了した採点者」を指します。
- 一部の被採点者しか提出していない採点者のデータは、提出済みの分も含めて
  集計から除外されます(DBからは削除されません)。
- eligible scorerが0人の場合は締切できません。
- 未完了の採点者がいる状態でも、ホストは強制的に締切できます
  (除外される人数が画面に表示されます)。
- 同点は competition ranking(1位, 1位, 3位)で表示します。

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
