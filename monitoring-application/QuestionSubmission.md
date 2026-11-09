# 問題二
即時資料分析與監控系統

**結論:** 題目列出的技術要求與功能需求皆已實作. 少數項目的做法與題目字面不同或較嚴格, 請見 [與題目不同或需注意之處](#與題目不同或需注意之處).

以下路徑皆相對於 `monitoring-application/`.

## 執行方式

需要 Docker 與 Docker Compose v2. 建立 `.env` 後, 填入 `MARIADB_ROOT_PASSWORD`, `MARIADB_PASSWORD` 與 `JWT_SECRET_KEY`:

```sh
cp .env.example .env
docker compose up -d --build
```

開啟 http://127.0.0.1:18501 登入. 若要測試三種角色的權限, `.env` 必須包含以下三個測試帳號. `.env.example` 已預填這些值, 後端首次啟動時會依 `.env` 建立帳號:

- **Admin:** `admin` / `Admin@12345`
- **User:** `user` / `User@12345`
- **Viewer:** `viewer` / `Viewer@12345`

可用 `docs/sample-records-1000.csv` 或 `docs/sample-records-1000.json` 測試批量導入. 完整說明請見 [README.md](README.md).

## 系統架構

- [docs/system-architecture.html](docs/system-architecture.html): 容器與服務之間的關係
- [docs/system-architecture-modules.html](docs/system-architecture-modules.html): 依五個功能模組劃分的元件與資料流

```text
瀏覽器 → 127.0.0.1:18501 → 前端 (Streamlit) → backend:8000 (FastAPI) → db:3306 (MariaDB) → db_data volume
                           └─────────────── monitoring-net ───────────────┘
```

## 驗證結果

以下結果於 2026-11-09 取得.

| 項目 | 指令 | 結果 |
| --- | --- | --- |
| 後端測試 (含一次性 MariaDB 11.7.2) | `backend/tests/run_integration.sh` | 702 通過, 5 略過 |
| 前端單元測試 | 在 `frontend/` 執行 `.venv/bin/python -m pytest tests` | 119 通過 |
| 瀏覽器測試 (Playwright, 對執行中的服務) | 在 `e2e/` 執行 `.venv/bin/python -m pytest` | 43 通過 |
| Swagger 副本 | 重新匯出後與 `docs/api-documentation.html` 比對 | 內容完全相同 |
| 圖表繪製 | 以 Viewer 登入並截圖檢查即時監控與資料分析頁面 | 折線圖, 柱狀圖與異常標記皆正常顯示 |

- 略過的 5 個後端測試都是需要手動開啟的大量資料測試 (`test_*_volume.py`), 本次未執行.
- 瀏覽器測試無法讀取圖表內容, 因此圖表繪製是人工檢查一次, 沒有自動化測試. 其他未涵蓋的範圍請見 [e2e/README.md](e2e/README.md).

## 技術要求

### 後端 (FastAPI)

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| 使用 FastAPI 框架 | ✅ | `backend/app/main.py` | `backend/tests/test_auth_api.py` 等 API 測試 |
| JWT 認證機制 | ✅ | `backend/app/auth/tokens.py`, `backend/app/auth/dependencies.py` | `backend/tests/test_auth_api.py` |
| RESTful API (CRUD) | ✅ | `backend/app/api/v1/records.py` | `backend/tests/test_records_api.py` |
| WebSocket 即時資料推送 | ✅ | `backend/app/api/v1/realtime.py`, `backend/app/realtime/hub.py` | `test_every_role_gets_snapshot_then_readings_over_websocket` |
| 使用 SQLAlchemy ORM, 禁止原生 SQL | ✅ | `backend/app/models/`, `backend/app/db/session.py` | 搜尋 `backend/app` 與 `backend/alembic`, 沒有 `text()`, `exec_driver_sql` 或 `op.execute` |
| 異步處理 | ✅ | `backend/app/api/v1/` 的路由, `backend/app/simulator/service.py` | `backend/tests/test_realtime_api.py` |
| 資料驗證 (Pydantic) | ✅ | 各模組的 `schemas.py`, 例如 `backend/app/records/schemas.py` | `backend/tests/test_records_api.py` |
| Swagger 文件 | ✅ | `/docs` (`backend/app/main.py`), `docs/api-documentation.html` | 容器內 `/docs` 回傳 200 |
| 錯誤處理 | ✅ | `backend/app/core/errors.py` | `backend/tests/test_errors.py` |
| 日誌記錄 | ✅ | `backend/app/core/logging.py`, `backend/app/syslog/` | `backend/tests/test_system_logs_api.py`, `backend/tests/test_syslog_writer.py` |

**說明:**

- 所有端點都位於 `/api/v1/` 之下, 以資源為路徑, 並依 HTTP 方法區分操作. 例如 `POST /records`, `GET /records`, `GET /records/{record_id}`, `PATCH /records/{record_id}`, `DELETE /records/{record_id}`.
- `/auth/register`, `/auth/login` 與 `/auth/logout` 是動作型路徑, 這是認證 API 的常見做法.
- 密碼以 Argon2id 雜湊保存 (`backend/app/auth/passwords.py`), JWT 使用 HS256 簽章.

### 資料庫 (MariaDB)

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| MariaDB 11.7 | ✅ | `compose.yaml` (`mariadb:11.7.2`) | 執行中的容器回報 11.7.2 |
| 所有資料庫操作使用 SQLAlchemy | ✅ | `backend/app/models/`, 各模組的 `service.py` | 同上方的原生 SQL 搜尋 |
| 資料庫遷移 (Alembic) | ✅ | `backend/alembic/versions/` (10 個版本), `backend/app/db/migrate.py` | 後端測試以一次性資料庫套用遷移 |
| 連接池 (SQLAlchemy Engine) | ✅ | `backend/app/db/session.py` | `backend/tests/test_db_status_api.py` |
| 異步操作 (asyncmy) | ✅ | `backend/app/core/config.py` (`mariadb+asyncmy`) | 後端測試 |
| 定期或條件觸發批次寫入 | ✅ | `backend/app/simulator/service.py` | `test_generates_for_every_device_and_writes_batches_as_system` |

**說明:**

- 連接池預設為 `pool_size=10`, `max_overflow=20`, 可用環境變數調整.
- 即時資料先放入記憶體緩衝區, 再批次寫入. 預設每 1 秒寫入一批 (`min(5 秒, 產生間隔)`), 累積 200 筆時提前寫入. 寫入失敗時保留資料並重試.
- 遷移在每次後端容器啟動時, 於開放連接埠前自動執行 (`backend/app/prestart.py`).

### 前端 (Streamlit)

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| Streamlit 互動式介面 | ✅ | `frontend/app.py`, `frontend/screens/` | `e2e/` |
| 登入/登出 | ✅ | `frontend/screens/login.py`, `frontend/core/session.py` | `test_login_survives_reload_and_logout_explains_itself` |
| 即時資料圖表更新 | ✅ | `frontend/screens/monitoring.py`, `frontend/core/live_feed.py` | `test_viewer_sees_the_live_window_update_without_a_reload` |
| 多頁面應用架構 | ✅ | `frontend/core/nav.py` | `frontend/tests/test_nav.py` |
| Session 狀態管理 | ✅ | `frontend/core/session.py` | `test_login_survives_reload_and_logout_explains_itself` |
| 與後端 API 整合 | ✅ | `frontend/core/api.py` | `e2e/` |

**說明:** 登入 Token 保存在 `session_state`, 並以 HttpOnly cookie 讓重新整理或開新分頁後仍保持登入.

### Docker

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| 多階段構建 | ✅ | `backend/Dockerfile` (`builder`, `test`, `runtime`), `frontend/Dockerfile` (`builder`, `runtime`) | 三個服務皆處於健康狀態 |
| Docker Compose 多容器編排 | ✅ | `compose.yaml` (`db`, `backend`, `frontend`) | `docker compose ps` |
| 容器間網路通訊 | ✅ | `compose.yaml` (`monitoring-net`) | `test_only_the_frontend_is_published_to_the_host` |
| Volume 資料持久化 | ✅ | `compose.yaml` (`db_data`) | 具名 volume, 只有 `docker compose down -v` 才會刪除 |
| 環境變數管理 | ✅ | `.env.example`, `backend/app/core/config.py` | `backend/tests/test_config.py` |

## 功能需求

### 1. 使用者管理模組

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| 使用者註冊 | ✅ | `POST /auth/register`, `frontend/screens/register.py` | `test_register_then_log_in_as_viewer` |
| 使用者登入 (JWT Token) | ✅ | `POST /auth/login`, `backend/app/auth/service.py` | `test_wrong_password_is_refused`, `backend/tests/test_auth_api.py` |
| 使用者角色 (Admin, User, Viewer) | ✅ | `backend/app/models/user.py` | `test_test_accounts_from_env_log_in_with_their_roles` |
| 權限控制 | ✅ | `backend/app/auth/permissions.py`, `frontend/core/nav.py` | `test_viewer_cannot_reach_add_or_range_pages_by_url`, `test_user_cannot_reach_admin_screens` |

**角色權限:**

| 操作 | Admin | User | Viewer |
| --- | --- | --- | --- |
| 即時監控, 資料列表, 資料分析, 下載 Excel | ✅ | ✅ | ✅ |
| 新增與批量導入 | ✅ | ✅ | - |
| 修改或刪除自己建立的記錄 | ✅ | ✅ | - |
| 修改或刪除他人的記錄 | ✅ | - | - |
| 系統管理頁面 | ✅ | - | - |

### 2. 資料管理模組

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| 創建記錄 (標題, 數值, 分類, 時間戳) | ✅ | `POST /records`, `frontend/screens/record_new.py` | `test_add_without_range_saves_a_normal_record` |
| 讀取 (分頁, 篩選, 排序) | ✅ | `GET /records`, `frontend/screens/records.py` | `test_edit_keeps_the_filter_and_delete_removes_the_record`, `backend/tests/test_records_api.py` |
| 更新 (僅限創建者或 Admin) | ✅ | `PATCH /records/{record_id}`, `backend/app/records/service.py` | `test_admin_edits_others_records_including_anomalies`, `test_list_actions_follow_role_owner_and_anomaly` |
| 刪除 (僅限創建者或 Admin) | ✅ | `DELETE /records/{record_id}`, `backend/app/records/service.py` | `test_edit_keeps_the_filter_and_delete_removes_the_record`, `test_list_actions_follow_role_owner_and_anomaly` |
| 批量導入 (CSV/JSON) | ✅ | `POST /records/imports`, `backend/app/records/importing.py`, `frontend/screens/records_import.py` | `test_csv_import_succeeds_with_the_importer_as_creator`, `test_json_import_marks_out_of_range_rows_without_asking` |

**說明:**

- 分頁使用游標 (`limit`, `cursor`). 可依標題, 數值, 分類或時間戳排序.
- 可依標題, 分類, 數值範圍, 時間範圍及是否異常篩選.
- 批量導入時, 任何一列有問題就整批拒絕, 並回報列號與原因. 已成功導入過的檔名不可再次導入. 單一檔案上限為 5 MB 與 10,000 筆資料列.

### 3. 即時監控模組

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| 模擬即時資料生成器 (每秒) | ✅ | `backend/app/simulator/` | `test_simulator_generates_every_second_by_default`, `test_each_device_reads_once_per_interval_with_generation_time` |
| WebSocket 推送即時資料 | ✅ | `/api/v1/realtime/ws`, `frontend/core/live_feed.py` | `test_live_window_is_pushed_over_a_websocket_and_redraws_every_second` |
| 即時折線圖 | ✅ | `frontend/core/charts.py` (`live_chart`) | 人工截圖檢查 |
| 即時柱狀圖 | ✅ | `frontend/core/charts.py` (`anomaly_count_bars`) | `test_monitoring_shows_the_anomaly_count_bar_chart`, 人工截圖檢查 |
| 異常告警 (超過閾值時標記) | ✅ | `backend/app/records/anomaly.py`, `frontend/screens/monitoring.py` | `test_about_five_percent_out_of_range_and_normal_values_never_touch_a_bound`, `test_recent_anomalies_are_newest_first_with_range` |

**說明:**

- 模擬器在每個 tick 為 4 個設備 (溫度, 壓力, 馬達轉速, 振動) 各產生一筆資料, 判定異常後同時推送與放入批次寫入緩衝區. 推送與儲存使用同一個判定結果.
- 數值小於或等於最小值, 或大於或等於最大值時標記為異常. Admin 可在 系統管理 → 異常範圍設定 調整各分類的範圍.
- 異常資料會顯示在折線圖的紅色菱形標記, 最新讀數區, 異常次數柱狀圖與異常統計卡片上.
- WebSocket 連線需要 JWT. Token 失效, 同一帳號在別處登入或登出時, 伺服器會中斷連線.

### 4. 資料分析模組

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| 統計分析 (總計, 平均, 最大, 最小) | ✅ | `GET /analytics/statistics`, `backend/app/analytics/service.py` | `test_statistics_are_per_category_with_no_total` |
| 時間範圍查詢 | ✅ | `start`, `end` 參數 (`backend/app/analytics/schemas.py`) | `test_export_matches_the_query_and_refuses_an_empty_range` |
| 分類資料聚合 | ✅ | `category` 參數, `backend/app/analytics/service.py` | `test_analysis_shows_anomaly_counts_per_category_as_a_bar_chart` |
| 資料趨勢圖表 | ✅ | `GET /analytics/trend`, `frontend/core/charts.py` (`trend_chart`) | `test_trend_refuses_too_many_points_and_the_list_holds_only_anomalies`, 人工截圖檢查 |
| 下載 Excel | ✅ | `GET /analytics/export`, `backend/app/exports/excel.py` | `test_viewer_queries_a_category_and_downloads_the_excel`, `backend/tests/test_exports_api.py` |

**說明:** Excel 檔有「摘要」與「明細」兩個工作表. 在畫面上先按「下載 Excel」產生檔案, 再按「儲存檔案」.

### 5. 系統管理模組 (Admin 專用)

| 要求 | 狀態 | 實作位置 | 驗證 |
| --- | --- | --- | --- |
| 查看所有使用者列表 | ✅ | `GET /users`, `frontend/screens/admin_users.py` | `test_admin_screens_render_for_an_admin`, `backend/tests/test_users_api.py` |
| 使用者權限管理 | ✅ | `PATCH /users/{user_id}/role` | `test_role_change_applies_on_the_next_request_and_self_change_is_refused` |
| 系統日誌查詢 | ✅ | `GET /logs`, `frontend/screens/admin_logs.py` | `test_failed_login_is_saved_to_the_system_log`, `backend/tests/test_system_logs_api.py` |
| 資料庫狀態監控 | ✅ | `GET /db-status`, `frontend/screens/admin_database.py` | `test_database_status_and_history_are_admin_only`, `backend/tests/test_db_status_api.py` |
| 即時資料的歷史資料查詢 | ✅ | `GET /history/records`, `frontend/screens/admin_realtime_history.py` | `test_database_status_and_history_are_admin_only`, `backend/tests/test_history_api.py` |

**說明:**

- 系統日誌可依時間範圍, 事件類型, 嚴重程度與使用者帳號篩選.
- 資料庫狀態包含可用性, 回應時間, 連接池使用狀況, 資料總筆數與最後批次寫入時間.

## 與題目不同或需注意之處

**與題目字面不同的做法:**

- **Swagger 無法從本機直接開啟.** 後端只在 Docker 網路內開放, 因此 `/docs` 僅限容器內存取. 請下載 `docs/api-documentation.html` 後以瀏覽器開啟. 檢視時需要網路連線, 且已關閉 "Try it out".
- **WebSocket 由 Streamlit 服務端連線, 不是由瀏覽器連線.** 同樣因為後端不對外開放. 前端服務端接收推送的資料後, 畫面每秒重繪一次.
- **模擬器每秒產生 4 筆, 而不是 1 筆.** 每個設備每秒一筆, 可用 `SIMULATOR_INTERVAL_SECONDS` 調整間隔.

**實作上的限制:**

- 部分寫入與查詢使用 SQLAlchemy 的語句建構方式, 例如 `insert(SystemLog)` 與 `select(func.get_lock(...))`, 而不是 `session.add()`. 這些都不是原生 SQL 字串.
- 註冊的帳號預設為 Viewer, 需由 Admin 調整角色. 同一帳號在別處登入時, 先前的登入會失效.
- 資料分析與歷史查詢單次最長 3 個月. Excel 明細最多 1,048,575 筆, 超過時會拒絕匯出.
- 模擬資料沒有保留期限. 以預設速率計算, 每天約 345,000 筆, 長期執行需要自行規劃清理.
- 前端只能以單一副本執行.
