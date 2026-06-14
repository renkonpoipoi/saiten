# MANZAI SCORE STUDIO

M1風の漫才採点アプリです。インストールなしで動きます。

## 使い方

1. `index.html` をブラウザで開きます。
2. 出場者と審査員を追加します。
3. 採点表に 0 から 100 点で入力します。
4. 「順位」表示でランキングを確認します。

入力内容はブラウザのローカルストレージに自動保存されます。

## 動的アプリ（採点入力者用）

採点入力者用の画面は Python サーバーで起動します。

```powershell
python server.py --host 127.0.0.1 --port 8765
```

起動後、ブラウザで `http://127.0.0.1:8765/input` を開きます。
現在はプロジェクト選択、採点者選択、採点入力画面まで実装しています。
採点項目は、独創性、実用性、UI/UXデザイン、技術力、拡張性の各20点と、一言コメントです。
3チーム分を1画面で入力でき、点数はスライダーで入力します。
3チームすべての必須項目が埋まると提出でき、提出ボタンを押すまでは同じ画面で修正できます。

管理者用画面は `http://127.0.0.1:8765/admin` です。
各採点者の提出状況を確認でき、全員提出後に「結果発表」を押すと集計結果の発表アニメーションを開始します。
管理者画面はアクセスコード制です。管理者コードは `data/admin_users.json` で変更できます。
管理者には、次のように `key` 付きURLを渡すと、コード入力なしでログインできます。

```text
http://127.0.0.1:8765/admin?key=admin-owner-4827
http://127.0.0.1:8765/admin?key=admin-friend-9153
```

同じWi-Fiの友人PCから使う場合は、ローカル専用の `127.0.0.1` ではなく、全端末から見える形で起動します。

```powershell
.\run_lan.ps1
```

または:

```powershell
python server.py --host 0.0.0.0 --port 8765
```

その後、あなたのPCのIPアドレスを使って共有します。

```text
http://あなたのPCのIPアドレス:8765/input
http://あなたのPCのIPアドレス:8765/admin?key=admin-owner-4827
http://あなたのPCのIPアドレス:8765/admin?key=admin-friend-9153
```

別ネットワークの友人にも使ってもらう場合は、Flask版を公開サーバーに載せます。

```powershell
pip install -r requirements.txt
python flask_app.py
```

Render/Railway などの公開サーバーでは `Procfile` の設定で `gunicorn` から起動します。
公開先では、採点者ごとに次のようなURLを共有できます。

```text
https://公開URL/input?project=m1-three-teams-2026&judge=judge-kaneko
https://公開URL/input?project=m1-three-teams-2026&judge=judge-sugawara
```

`project` には `data/scoring_projects.json` のプロジェクトID、`judge` には審査員IDを入れます。
このURLを使うと、プロジェクト選択と審査員選択を飛ばして、その審査員の採点画面を直接開けます。
採点結果は公開サーバーでは `data/scores.sqlite3` に保存されます。
公開先のファイル保存が再起動で消えるサービスを使う場合は、永続ディスクを `data/` に割り当てるか、`SCORE_DB_PATH` 環境変数で永続保存先を指定してください。

公開サーバーでは次の環境変数も設定してください。

```text
SECRET_KEY=長いランダム文字列
SCORE_DB_PATH=/永続保存できる場所/scores.sqlite3
```

管理者画面も使う場合は `https://公開URL/admin?key=...` を共有します。

## ファイル

- `index.html`: 画面構造
- `styles.css`: デザイン
- `app.js`: 採点、順位計算、保存、読み込み
- `server.py`: 動的アプリ用サーバー
- `flask_app.py`: 公開・デプロイしやすいFlask版サーバー
- `requirements.txt`: Flask版の依存関係
- `Procfile`: 公開サーバー用の起動設定
- `run_lan.ps1`: 同じWi-Fi内で共有するための起動スクリプト
- `scorer.html`: 採点入力者用画面
- `scorer.css`: 採点入力者用デザイン
- `scorer.js`: 採点入力者用の画面遷移とAPI通信
- `admin.html`: 管理者用画面
- `admin_login.html`: 管理者ログイン画面
- `admin.css`: 管理者用デザイン
- `admin.js`: 提出状況表示と結果発表アニメーション
- `data/scoring_projects.json`: 採点プロジェクト定義
- `data/scores.json`: 旧形式の採点入力データ。公開用Flask版では初回起動時にSQLiteへ移行します
- `data/scores.sqlite3`: 公開用Flask版の採点入力データ
- `data/admin_users.json`: 管理者アクセスコード
- `data/admin_sessions.json`: 管理者ログインセッション
- `assets/stage-bg.png`: 背景画像

## 機能

- 出場者の追加、削除、並び替え
- 審査員の追加、削除、並び替え
- 0 から 100 点の採点
- 合計、平均、順位の自動計算
- 同点トップの表示
- サンプルデータ投入
- JSON書き出し、読み込み
- ローカルストレージ自動保存
