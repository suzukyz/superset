# Superset 自動Issue検証デモ — 検証レポート

## 概要 (Summary)

Apache Superset の実行中UIを使い、2件の既知不具合を**エンドツーエンドで再現**しました。両不具合とも**再現 (reproduced)** と判定します。証拠としてスクリーンショット・生成SQL・エラーログ・録画を取得しました。

- **Issue A** (fork issue #4 / apache/superset#42243): Tableチャートのサーバーページネーションで、ページサイズ選択肢が壊れる → **再現**
- **Issue B** (fork issue #5 / apache/superset#42386): VARCHAR列を temporal 指定すると `DATE_TRUNC` が VARCHAR に適用され PostgreSQL でクエリ失敗 → **再現**

> ⚠️ 注記: これは検証（再現確認）のみです。製品コードの修正は行っておらず、GitHub への投稿も行っていません。

## 環境 (Environment)

| 項目 | 値 |
| --- | --- |
| リポジトリ | `/home/ubuntu/repos/superset` |
| ブランチ | `devin/1785596209-issue-verification` |
| コミット | `fae84ba218d8eb922b1a38518f1a373dced52609` (`feat: add Devin automated issue verification workflow and playbook`) |
| 製品コード変更 | なし（CIワークフロー + playbook ドキュメントのみ差分） |
| Superset バージョン | `0.0.0-dev`（master相当、上記コミット） |
| 起動方法 | `docker-compose-light.yml` スタック |
| バックエンドコンテナ | `superset-superset-light-1` (直接IP `172.18.0.2:8088` を使用) |
| DBコンテナ | `superset-db-light-1` |
| データベース | PostgreSQL 17.10 (`superset_light`, スキーマ `public`, UI接続名 `examples`) |
| ログイン | admin / admin |
| UIアクセス | `http://172.18.0.2:8088`（webpack proxy `localhost:9001` が不安定なため直接バックエンドを使用） |

### 環境上の逸脱・注意点 (Deviations)

- light スタックの webpack dev server (`localhost:9001`) は、新規の `/api/v1/chart/data` POST がタイムアウトする不安定さがあった。バックエンドコンテナIP `172.18.0.2:8088`（ビルド済みアセットを直接配信）を使うことで安定して検証を完遂した。これはテスト環境の制約であり、対象不具合とは無関係。
- 途中で Chrome がクラッシュしたため再起動した（環境要因、対象不具合とは無関係）。

---

## Issue A: Tableチャートのサーバーページネーションでページサイズ選択肢が壊れる

**判定: 再現 (reproduced)**

### 実行手順 (Exact steps)

1. 12行を返す仮想データセット/クエリ（`SELECT * FROM public.flights LIMIT 12`）で標準 Table チャートを準備。
2. Explore で **Row limit = 10000**、**Server pagination = ON**、**Server Page Length = 20** に設定して実行。
3. 12行がすべて1ページに表示されることを確認。
4. 「entries per page」セレクタを開き、選択肢を確認。
5. `10` を選択し、ページ数の変化を確認。
6. 再度セレクタを開き、選択肢を確認。

### 観察結果 (Observed) と証拠

- **A1**: 12行すべてが1ページに表示され、セレクタを開くと選択肢が `0`(All) と `10` のみに限定され、**設定した `20` が存在しない**。

  ![A1 12行1ページ表示](https://app.devin.ai/attachments/ab72ba11-a25e-4f80-b915-82b7cd5f4e4f/A1_onepage_12rows.png)
  ![A1 セレクタに20が無い](https://app.devin.ai/attachments/b93cb360-54a9-4f87-a6db-b97c1b4a2524/A1_selector_20_absent.png)

- **A2**: `10` を選択すると **2ページ** になる。再度セレクタを開くと **`10` のみ**で、全件表示 (`0`/All) に戻す選択肢が消えており、ページ更新なしでは1ページ表示に戻せない。

  ![A2 10選択で2ページ](https://app.devin.ai/attachments/e09d7691-e4f3-40cd-9185-c382050c44d7/A2_two_pages_after_10.png)
  ![A2 セレクタが10のみ](https://app.devin.ai/attachments/1161e081-cd72-4766-b3e7-8d04836cb2e8/A2_selector_only_10.png)

### コードパスのサニティチェック (Reported cause)

報告された疑わしい箇所を確認。行番号は現行チェックアウトでは以下の通り（報告の L388-395 とは異なるが、ロジックは一致）:

- `superset-frontend/plugins/plugin-chart-table/src/TableChart.tsx` (L463-470):
  ```tsx
  const pageSizeOptions = useMemo(() => {
    const getServerPagination = (n: number) => n <= rowCount;
    return (
      serverPagination ? SERVER_PAGE_SIZE_OPTIONS : PAGE_SIZE_OPTIONS
    ).filter(([n]) =>
      serverPagination ? getServerPagination(n) : n <= 2 * data.length,
    ) as SizeOption[];
  }, [data.length, rowCount, serverPagination]);
  ```
  `SERVER_PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200]`（`consts.ts`）を `n <= rowCount` でフィルタ。`rowCount = 12` のため **`10` のみが残り、設定値 `20` が除外される**。

- `superset-frontend/plugins/plugin-chart-table/src/DataTable/DataTable.tsx` (L457-473):
  ```tsx
  const serverPageSize = serverPaginationData?.pageSize ?? initialPageSize; // = 20
  ...
  const foundPageSizeIndex = pageSizeOptions.findIndex(
    ([option]) => option >= resultCurrentPageSize,
  );
  if (foundPageSizeIndex === -1) {
    resultCurrentPageSize = 0; // 20が見つからず 0(全件1ページ) に強制
  }
  ```
  設定値 20 がフィルタ済み選択肢に無いため、現在のページサイズが `0`（全件1ページ表示）に強制される。10を選ぶと選択肢から 0/All に戻る術が無くなる。

**結論**: 観察された挙動は報告されたコードパスと一致。**再現。**

---

## Issue B: VARCHAR列を temporal 指定 → PostgreSQLで DATE_TRUNC が失敗

**判定: 再現 (reproduced)**

### 実行手順 (Exact steps)

1. DBコンテナ内 psql で `superset_light` に物理テーブルを作成しサンプル行を投入:
   ```sql
   CREATE TABLE website_events (
     id serial PRIMARY KEY,
     event_name varchar(50),
     user_count integer,
     event_timestamp varchar(30)
   );
   -- 5行 (event_timestamp はタイムスタンプ形式の文字列, 例 '2025-01-01 10:00:00')
   ```
2. UIで `website_events` の**物理データセット**を作成 (datasource ID 22, 接続 `examples`, スキーマ `public`)。
3. データセット編集 → Columns タブで **`event_timestamp` (VARCHAR(30)) に「Is temporal」を有効化**して保存。
4. Line (Time-series) チャートを作成: **X-axis = event_timestamp**、**Time Grain = Day**、**Metric = SUM(user_count)**。
5. 「Create chart / Update chart」で実行。
6. 「View query」で生成SQLを確認。

### 観察結果 (Observed) と証拠

- **前提**: `event_timestamp` (VARCHAR(30)) を temporal 指定。

  ![B 事前設定 temporal on VARCHAR](https://app.devin.ai/attachments/deb956cb-f665-4af0-b67b-92638efd40d7/B_temporal_varchar.png)

- **B1**: チャート実行で **Data error** が発生:
  ```
  Error: function date_trunc(unknown, character varying) does not exist
  LINE 1: SELECT DATE_TRUNC('day', event_timestamp) AS event_timestamp...
                            ^
  HINT:  No function matches the given name and argument types. You might need to add explicit type casts.
  ```

  ![B1 date_trunc エラー](https://app.devin.ai/attachments/de274cd7-b984-4e5d-b7c6-4d546eed147e/B1_data_error_datetrunc.png)

- **B2**: 「View query」の生成SQLに `DATE_TRUNC('DAY', event_timestamp)` が含まれる（VARCHAR列にキャストなしで適用）:
  ```sql
  SELECT
    DATE_TRUNC('DAY', event_timestamp) AS event_timestamp,
    SUM(user_count) AS "SUM(user_count)"
  FROM public.website_events
  GROUP BY
    DATE_TRUNC('DAY', event_timestamp)
  ORDER BY
    "SUM(user_count)" DESC
  LIMIT 10000
  ```

  ![B2 生成SQL DATE_TRUNC](https://app.devin.ai/attachments/81c230ae-e73d-4fa3-ba99-583eb44794e2/B2_viewquery_datetrunc.png)

**結論**: VARCHAR列を temporal 指定すると、Superset は明示的キャストなしで `DATE_TRUNC('day', <varchar>)` を生成し、PostgreSQL が型不一致でクエリを拒否する。報告どおり。**再現。**

---

## 所要時間 (Wall-clock, 概算)

- 環境立ち上げ・切り分け（webpack proxy不安定 → 直接バックエンド利用、Chrome再起動含む）: 相当時間を要した（環境要因）。
- 検証セッション全体（Issue A + Issue B、環境troubleshooting込み）: 約 43 分。
  - Issue A（チャート構築・セレクタ再現・コード確認）: 概算 25 分（環境切り分けを含む）。
  - Issue B（テーブル作成・データセット設定・チャート実行・SQL/エラー取得）: 概算 15 分。

> 正確な per-issue の分計測は環境troubleshootingと重なるため概算。

## 証拠ファイル (Evidence paths, ローカル)

- 録画: `/home/ubuntu/screencasts/rec-8c9c3a4b-ab51-448a-bb75-02e47e07d208/rec-8c9c3a4b-ab51-448a-bb75-02e47e07d208-edited.mp4`
- Issue A: `A1_onepage_12rows.png`, `A1_selector_20_absent.png`, `A2_two_pages_after_10.png`, `A2_selector_only_10.png`
- Issue B: `B_temporal_varchar.png`, `B1_data_error_datetrunc.png`, `B1_error_zoom.png`, `B2_viewquery_datetrunc.png`, `B2_sql_zoom.png`
- テキスト: `B_error.txt`, `B_generated_sql.sql`
- ディレクトリ: `/home/ubuntu/artifacts/`

## 結論

両不具合とも実UIでエンドツーエンドに**再現**しました。環境上の逸脱（webpack proxy不安定→直接バックエンド利用、Chrome再起動）はありましたが、対象不具合の挙動には影響していません。
