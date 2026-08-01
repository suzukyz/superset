# Superset Issue検証プラン（自動Issue検証デモ）

環境: docker-compose-light.yml スタック、UI: http://localhost:9001 (webpack dev server → superset-light:8088)、admin/admin ログイン済み想定。DB: Postgres 17 (superset_light、examplesテーブルはpublicスキーマ)。

## Issue A (#4 / apache#42243): Tableチャートのサーバーページネーションでページサイズ選択肢が壊れる

コード根拠: `plugins/plugin-chart-table/src/TableChart.tsx` L463-470 が `SERVER_PAGE_SIZE_OPTIONS`([10,20,50,100,200], consts.ts L31) を `n <= rowCount` でフィルタ。rowCount=12なら残るのは[10]のみ。`DataTable.tsx` L457-473: serverPageSize=20が選択肢に無いため `resultCurrentPageSize = 0`（全件1ページ表示）。

手順:
1. SQL Labで `SELECT * FROM flights LIMIT 12` を実行し、結果から「Create Chart」→ Table チャート(virtual dataset)。
2. Explore で Query Mode = Raw records（またはそのまま）、Row limit=10000、Server pagination ON、Server Page Length=20 に設定して実行。
3. アサーションA1: 12行すべてが1ページに表示され、下部の「entries per page」セレクタを開くと選択肢が `[0(All)] と [10]` 程度に限定され、設定した 20 が存在しない → バグ再現（スクリーンショット）。
4. セレクタで 10 を選択 → 2ページ表示になる。
5. アサーションA2: セレクタを再度開くと 10 のみで、全件表示(0/All)に戻す選択肢が消えている → バグ再現（スクリーンショット）。
- 判定: 20が選択肢に無い＆10選択後に戻せない状態が観察できれば「reproduced」。20が表示され選択できれば「not reproduced」。

## Issue B (#5 / apache#42386): VARCHAR列をtemporal指定 → PostgreSQLで DATE_TRUNC が失敗

手順:
1. dbコンテナ内psqlで superset_light に `website_events` テーブル作成 + サンプル行INSERT（event_timestampはvarchar(30)にタイムスタンプ文字列）。
2. UIで Datasets → + Dataset → examples DB / publicスキーマ / website_events で物理データセット作成。
3. データセット編集で event_timestamp 列に「Is temporal」をON、保存。
4. 新規チャート: Line/Time-series、Time column=event_timestamp、Time grain=Day、Metric=SUM(user_count)、実行。
5. アサーションB1: クエリがエラーになり、エラーメッセージに DATE_TRUNC と型不一致 (`function date_trunc(unknown, character varying) does not exist` 等) が含まれる（スクリーンショット）。
6. アサーションB2: 「View query」で生成SQLに `DATE_TRUNC('day', event_timestamp)` が含まれることを確認（スクリーンショット + SQL全文保存）。
- 判定: 上記エラー＆SQLが確認できれば「reproduced」。チャートが正常描画されれば「not reproduced」。

各Issueで test_start / assertion アノテーションを分け、所要時間（wall clock）を記録する。
