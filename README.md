# Score Input App

採点者が点数を入力するためだけの公開用アプリです。
結果発表画面は別の `result` アプリで公開します。
resultとsaitenを統合する可能性もあり

## 公開時の画面

入力画面:

```text
https://あなたのsaitenアプリURL/input
```

審査員ごとに直接開く場合:

```text
https://あなたのsaitenアプリURL/input?project=m1-three-teams-2026&judge=judge-kaneko
https://あなたのsaitenアプリURL/input?project=m1-three-teams-2026&judge=judge-sugawara
```

`project` には `data/scoring_projects.json` のプロジェクトID、`judge` には審査員IDを指定します。

## 入力可能時間

入力できる時間は日本時間で固定しています。

```text
2026年7月2日 14:30〜16:10
```

この時間外は画面の入力欄が無効になり、APIに直接送っても保存・提出は拒否されます。

## Result アプリとの連動

この `saiten` アプリは入力専用ですが、別公開の `result` アプリが結果を読むために次のAPIは残しています。

```text
/api/projects
/api/result/summary
```

`result` アプリ側の環境変数には、この `saiten` アプリのURLを入れてください。

```text
SCORE_SOURCE_BASE_URL=https://あなたのsaitenアプリURL
```

## Render 設定

```text
Build Command: pip install -r requirements.txt
Start Command: gunicorn flask_app:app --bind 0.0.0.0:$PORT --workers 1
```

採点データは SQLite に保存されます。Render で再起動後も残したい場合は、永続ディスクを使い、必要に応じて保存先を指定してください。

```text
SCORE_DB_PATH=/永続保存できる場所/scores.sqlite3
```

## 主なAPI

```text
GET  /api/projects
GET  /api/entry-window
GET  /api/scores
POST /api/judge-session
POST /api/scores
POST /api/submit
GET  /api/result/summary
```

---

## 新アプリ基盤(Phase 1: 移行作業中)

上記は旧実装(`flask_app.py` / `server.py` / `score_storage.py`)の説明です。
現在、統合・刷新後の「M1風採点アプリ」への移行作業を`app/`配下で進めています。
Phase 1時点ではモデル定義とDB接続の基盤のみで、ルーティング(画面/API)はまだ
実装されていません。旧実装は移行完了まで並行して残し、Phase 6でまとめて削除
する予定です。

### 実装計画とのPKの差異

実装計画のDB設計例はPostgres向けに`BIGSERIAL`/`BIGINT`を提示していました
が、実装ではSQLite/PostgreSQL双方の単純性を優先して全PK/FKに`Integer`を
採用しています。本アプリの規模ではPostgreSQL `INTEGER`の上限(約21億)で
十分であり、SQLiteの`INTEGER PRIMARY KEY`(ROWIDエイリアスによる自動採番)
との相性も優先しました。

### APP_ENV(必須)

新アプリは起動時に環境変数`APP_ENV`を必須とします。`development` /
`testing` / `production`のいずれかを明示的に指定してください。未設定また
は不明な値の場合は**起動時に例外を送出して落ちます**(developmentへの暗黙
フォールバックは行いません)。

```text
APP_ENV=development   # ローカル開発。DATABASE_URL未設定ならdata/dev.sqlite3を使う
APP_ENV=testing        # pytest用。SQLiteインメモリ
APP_ENV=production     # 本番。DATABASE_URL必須、未設定なら起動失敗
```

### SECRET_KEY(必須・全環境)

全環境で`SECRET_KEY`を必須とします。未設定時のランダム生成フォールバック
は行いません(旧`flask_app.py`にあった`secrets.token_urlsafe(32)`の毎起動
生成は廃止しました)。

```text
SECRET_KEY=十分に長いランダムな文字列
```

### DATABASE_URL / MIGRATION_DATABASE_URL(本番はNeon Free PostgreSQLを想定)

本番DBはRender Free PostgreSQL(30日で期限切れ)ではなく、**Neon Free
PostgreSQL**を使う方針です。RenderのfilesystemはPersistent Diskを持たない
ため、永続データは全てNeonに保存し、ローカルSQLiteは開発/テスト専用としま
す。

```text
DATABASE_URL=postgresql://user:pw@ep-xxx-pooler.region.aws.neon.tech/db?sslmode=require
MIGRATION_DATABASE_URL=postgresql://user:pw@ep-xxx.region.aws.neon.tech/db?sslmode=require
```

- `DATABASE_URL`: アプリruntime用。Neonの**pooled connection**(ホスト名に
  `-pooler`を含む)を指定します。
- `MIGRATION_DATABASE_URL`: `flask db upgrade`(Alembic)用。Neonの**direct
  connection**(`-pooler`を含まない)を指定します。未設定時は`DATABASE_URL`
  にフォールバックします。
- `postgres://`形式で渡された場合も含め、内部で`postgresql+psycopg://`へ
  自動的に正規化されます(`?sslmode=require`等のクエリパラメータはそのまま
  保持されます)。

**Neonへのmigration適用は現時点では未実施です。** Phase 1はローカル
SQLite(`data/dev.sqlite3`)のみを対象に検証しており、本番Neon DBへの接続・
migration適用はホストの明示的な許可を得てから行います。

### ローカル開発でのセットアップ

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # pytestを含む開発用一式
export FLASK_APP=wsgi.py APP_ENV=development SECRET_KEY=dev-local-secret
.venv/bin/flask db upgrade   # data/dev.sqlite3 にスキーマを作成
.venv/bin/python -m pytest tests/
```

### migrationの安全な運用方針(将来Phase 7で本番適用する際の前提)

- migrationは原則backward-compatibleな**expand/contract方式**とします。
  カラム追加等の無害な変更は先行適用してよいですが、既存カラムの削除・
  rename等の破壊的変更は、新コードのdeployと同一deployでは行いません。
- Render Build Command失敗時にRenderは新バージョンを起動せず旧バージョン
  を継続しますが、**Build Command内で部分的に適用されたDBスキーマ変更は
  自動でロールバックされません**。上記のexpand/contract方針はこの前提を
  踏まえたものです。
- 初回の空DB構築(最初の`flask db upgrade`)はこの制約の対象外です。
