# Score Input App

採点者が点数を入力するためだけの公開用アプリです。
結果発表画面は別の `result` アプリで公開します。

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
