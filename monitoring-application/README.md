# MonitoringDashboard

## 執行

需要 Git、Docker 與 Docker Compose v2. 從 GitHub 取得專案:

```sh
git clone https://github.com/CJsGit-tech/ImageSegmentation-Monitoring.git
cd ImageSegmentation-Monitoring/monitoring-application
```

建立環境變數檔, 再填入資料庫密碼與 `JWT_SECRET_KEY` (三個測試帳號已有範例值):

```sh
cp .env.example .env
```

建置並啟動資料庫、後端與前端:

```sh
docker compose up -d --build
```

確認三個服務都處於健康狀態:

```sh
docker compose ps
```

| 服務 | 位址 (預設) |
| --- | --- |
| 前端 | http://127.0.0.1:18501 |
| 後端 API | http://127.0.0.1:18000 |
| 後端 Swagger | http://127.0.0.1:18000/docs |
| 資料庫 | 不對 Docker 外部開放 |

啟動後:

1. 開啟 http://127.0.0.1:18501, 以下方說明中三個測試帳號的任一個登入.
2. 以 User 或 Admin 身分, 在側邊欄進入 資料 → 批量導入, 上傳 `docs/sample-records-1000.csv` 以載入 1000 筆範例記錄.
3. 即時監控 頁面透過 WebSocket 接收即時資料, 每秒更新一次. 模擬資料會在啟動後約 1 秒出現.

查看後端日誌:

```sh
docker compose logs -f backend
```

停止服務並保留資料庫:

```sh
docker compose down
```

資料庫資料存放在 `db_data` volume, 執行 `docker compose down` 後仍會保留. `docker compose down -v` 則會刪除資料.

- 每個 `docker compose` 指令都會讀取 `.env`, 包含 `down`. 缺少必填變數時會停止, 並顯示該變數名稱.
- MariaDB 只在第一次建立 volume 時套用 `MARIADB_*` 帳密. 之後若要變更, 需同時在資料庫中修改, 或重新建立 volume, 但這會刪除資料.
- 每次後端容器啟動時, 都會自動執行資料庫遷移.
- 後端會產生 4 個設備的模擬讀數, 每個設備每隔 `SIMULATOR_INTERVAL_SECONDS` 產生一筆 (預設為 1, 即每個設備每秒一筆). 此值在後端啟動時讀取, 修改後請重新啟動: `docker compose up -d backend`. 模擬讀數會保留在資料庫中, 沒有時間限制; 以預設速率計算, 每天約 345,000 筆.
- 監控畫面的即時視窗透過後端 WebSocket (`/api/v1/realtime/ws`) 接收讀數: Streamlit 服務端會為每位顯示即時視窗的使用者維持一條連線, 並每秒重繪. 沒有人查看的連線會在 30 秒後關閉. `GET /api/v1/realtime/window` 透過 REST 回傳相同的快照.
- 三個測試帳號各對應一種角色, 都來自 `.env`: `INITIAL_ADMIN_*` (Admin)、`INITIAL_USER_*` (User) 與 `INITIAL_VIEWER_*` (Viewer). 這六個變數都必須設定, 三個帳號名稱也不可相同. `.env.example` 提供範例值 (`admin` / `Admin@12345`、`user` / `User@12345`、`viewer` / `Viewer@12345`), 用於本機測試以外的用途時請更改. 帳號不存在時, 會在後端啟動時建立. 帳號建立後不再套用這些值, 因此修改它們不會改變該帳號的密碼或角色. 不符合註冊規則的密碼仍可使用, 但後端會記錄 `initial_<role>_weak_password` 警告, 並列出缺少的條件. 請使用至少 8 個字元, 且包含大寫字母、小寫字母、數字與符號的密碼.
- 系統日誌 (Admin, `GET /api/v1/logs`) 會保存登入、登出、權限拒絕、角色變更、記錄變更、導入結果、範圍與單位變更, 以及系統錯誤, 但不保存模擬讀數. 超過 `LOG_RETENTION_DAYS` (預設 365) 的資料列會自動移除, 時機是後端啟動時, 之後最多每小時一次. 修改後請重新啟動後端: `docker compose up -d backend`.
- 每個系統日誌事件也會以 JSON 行的形式寫入後端的 stdout. 資料列約每秒批次寫入資料庫一次, 因此後端當機時, 資料庫可能遺失約最後一秒的資料列, 但 stdout 不受影響. 資料庫無法使用時, 資料列會暫存在記憶體中 (最多 10,000 筆, 超過時會捨棄最舊的資料並發出警告), 待資料庫恢復後再寫入.

## 角色

| 角色 | 權限 |
| --- | --- |
| Admin | 所有功能, 包含系統管理頁面. 可修改或刪除任何人的一般記錄 |
| User | 瀏覽、新增與批量導入. 只能修改或刪除自己建立的記錄 |
| Viewer | 瀏覽即時監控、資料列表與資料分析, 並下載 Excel 檔 |

- 在註冊頁建立的帳號預設為 Viewer. Admin 可在 系統管理 → 使用者管理 修改其角色.
- 同一帳號在別處登入時, 先前的登入會失效.
- 被標記為異常的記錄任何人都不能修改或刪除, 包含 Admin.

## API 文件

[`docs/api-documentation.html`](docs/api-documentation.html) 是 API 文件的 Swagger UI 副本. 請下載後以瀏覽器開啟. 檢視時需要網路連線以載入 Swagger UI, 但不需要執行後端. 此副本已關閉 "Try it out", 若要發送請求, 請使用上方的 Swagger 位址. 所有端點都位於 `/api/v1/` 之下. WebSocket (`/api/v1/realtime/ws`) 未列於文件中, 因為 OpenAPI 不涵蓋 WebSocket.

修改 API 後, 請在此資料夾重新產生檔案:

```sh
docker build --target test -t monitoring-dashboard-backend-test backend
docker run --rm -v "$PWD/docs:/out" monitoring-dashboard-backend-test python -m scripts.export_api_docs /out/api-documentation.html
```

## 前端

前端是 Streamlit 應用程式. `frontend/asgi.py` 是伺服器進入點, `frontend/app.py` 是每個頁面都會經過的外殼. 畫面位於 `frontend/screens/`. 請勿將該資料夾改名為 `pages/`: 否則在伺服器啟動後的第一個工作階段, Streamlit 會在側邊欄列出所有檔案, 包含管理頁面.

登入狀態存放在 HttpOnly cookie `md_session` 中, 因此重新整理或開新分頁後仍會保留. 在純 HTTP 下它沒有 `Secure` 屬性, 而瀏覽器會把 cookie 傳送到同一主機的所有連接埠. 除非前端位於有 TLS 的獨立主機名稱之後, 否則請維持預設的 `HOST_BIND_ADDRESS=127.0.0.1`. 前端只能以單一副本執行.

## 測試

```sh
docker build --target test -t monitoring-dashboard-backend-test backend
docker run --rm monitoring-dashboard-backend-test
```

這不需要資料庫, 因此需要資料庫的測試會被略過並說明原因. 若要執行全部測試, 請使用下方腳本. 它會在臨時網路上啟動一次性的 MariaDB 容器, 完成後兩者都會移除:

```sh
backend/tests/run_integration.sh
```

實際操作應用程式的瀏覽器測試位於 `e2e/`. 設定方式請見 [e2e/README.md](e2e/README.md).
