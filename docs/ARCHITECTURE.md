# 構成

画面は `web/index.html`・`web/app.js`・`web/style.css`、ローカルHTTPは `web_ui.py` です。HTTPはループバック限定、起動時トークンとセッションCookie、Origin/Host検証を使用します。サーバーをLANやインターネットへ公開する構成ではありません。

| 層 | ファイル | 責務 |
|---|---|---|
| 設定 | `settings.py`, `setup_api.py` | 初期設定、ルームの全文登録、設定の世代管理 |
| 投稿 | `posting.py`, `recurrence.py`, `templates.py` | 下書き、承認、予約、重複防止、状態管理 |
| 送信 | `posting_worker.py`, `posting_adapter.py`, `native_transport.py` | 排他、LINE画面との結合、本文照合、1回の送信操作 |
| 画像投稿 | `media.py`, `media_adapter.py`, `media_delivery.py` | 内容ハッシュ、画像変換、本文と画像の部分成功の記録 |
| 履歴 | `collector.py`, `mobile_export.py`, `archive_features.py` | 追記保管、元データ保持、検索とキーワード |
| 定期取得 | `worker.py`, `collection_schedule.py` | 日次取得、PC復帰後の補完 |
| 再開 | `screen_archive.py`, `screen_resume.py`, `collection_retry.py` | 画面本文の連続性、途中保存、上限付き再開 |
| Windows操作 | `native/`, `posting-native/` | UI Automation・Win32、ウィンドウ・入力欄確認 |

## 投稿状態

`draft → scheduled → preparing → send_intent → local_confirmed / needs_review`

送信操作より前に `send_intent` をコミットします。送信途中のプロセス終了は、次回に `needs_review` として回収します。自動再送はしません。送信前の検証失敗は `blocked` になります。確認用トークンは内容・版・時刻に結び付け、有効期限を設けています。

新規DBの投稿は一時停止です。設定変更は投稿と取得の両ロックを取得します。投稿ワーカーは投稿ロック、取得ロック、ユーザー単位のデスクトップロックの順で取得します。取得ワーカーは取得ロック、デスクトップロックの順です。複数のToolkitコピー間の画面競合も同じデスクトップロックで止めます。他の自動操作ソフトはこの排他に参加しないため、同時運用しないでください。

## 変更する場所

- 見た目：`web/`。UI操作のテストを追加します。
- 対象ルーム：アプリ設定。ソースへルーム名を埋め込まないでください。
- 取得時刻：アプリ設定。`window_minutes` は旧形式との整合用の値です。現在は指定時刻以降に当日未取得分を補完します。
- 投稿可能時間：Pythonの `effective_time`・`claim` とC#の `LineDeliveryPolicy`、UI表示、テストをまとめて変更する必要があります。
- LINEの画面変更：C#の識別条件とラベル画像。宛先や本文が照合できない状態で座標クリックへ緩和しないでください。

## 公開用ファイル

ネイティブ操作ソースは切り出し時点の生成済みC#を直接管理し、`build.py` がコンパイルします。個人用の兄弟フォルダーからソースを読み出す生成処理はありません。

`release-files.json` は配布用の明示的な一覧です。新しいソースや説明書を加える場合は一覧を更新し、`audit_release.py` と `run_tests.py` を実行してください。実行データや `.venv`、生成バイナリは一覧へ加えません。
