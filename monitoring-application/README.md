# MonitoringDashboard

## 技術棧

| 項目 | 技術 |
|---|---|
| 程式語言 | Python 3.13 |
| 後端 | FastAPI 0.162, Pydantic 2.14, Uvicorn 0.54 |
| 認證 | JWT (PyJWT 2.15), Argon2id 密碼雜湊 |
| 資料庫 | MariaDB 11.7.2 |
| ORM 與遷移 | SQLAlchemy 2.1 (asyncmy 0.2 非同步驅動), Alembic 1.20 |
| 前端 | Streamlit 1.67, Altair 6.3 |
| Excel 匯出 | XlsxWriter 3.2 |
| 部署 | Docker 多階段構建, Docker Compose v2 |

## 系統架構

- [`docs/system-architecture.html`](docs/system-architecture.html): 容器與服務之間的關係
- [`docs/system-architecture-modules.svg`](docs/system-architecture-modules.svg): 依五個功能模組劃分的元件與資料流 (亦有 [HTML 版本](docs/system-architecture-modules.html))

HTML 檔案請下載後以瀏覽器開啟.

## 執行

執行應用程式需要 Git、Docker 與 Docker Compose v2 (執行測試所需的其他工具請見[測試](#測試)). 從 GitHub 取得專案:

```sh
git clone https://github.com/CJsGit-tech/ImageSegmentation-Monitoring.git
cd ImageSegmentation-Monitoring/monitoring-application
```

建立環境變數檔:

```sh
cp .env.example .env
```

在 `.env` 填入 `MARIADB_ROOT_PASSWORD`、`MARIADB_PASSWORD` 與 `JWT_SECRET_KEY`, 否則系統無法啟動. `JWT_SECRET_KEY` 至少需要 32 個字元, 可用 `openssl rand -hex 32` 產生.

`.env.example` 已預填三個測試帳號, 各對應一種角色, 後端首次啟動時會依 `.env` 建立:

- **Admin:** `admin` / `Admin@12345`
- **User:** `user` / `User@12345`
- **Viewer:** `viewer` / `Viewer@12345`

建置並啟動資料庫、後端與前端:

```sh
docker compose up -d --build
```

確認三個服務都處於健康狀態. 前端剛啟動時可能短暫顯示 `health: starting`, 請等幾秒後再執行一次, 直到三個服務都顯示 `healthy`:

```sh
docker compose ps
```

| 服務 | 位址 (預設) |
| --- | --- |
| 前端 | http://127.0.0.1:18501 |
| 後端 API | 不對 Docker 外部開放, 只有 `monitoring-net` 網路內的容器可連線 (`http://backend:8000`) |
| 資料庫 | 不對 Docker 外部開放, 只有 `monitoring-net` 網路內的容器可連線 (`db:3306`) |

啟動後:

1. 開啟 http://127.0.0.1:18501, 以上述三個測試帳號的任一個登入.
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

### 補充說明

- `docker compose` 指令會讀取 `.env`. 缺少必填變數時, `up`、`build` 與 `config` 會停止, 並顯示該變數名稱. `ps`、`logs`、`exec`、`restart`、`stop` 與 `down` 則不受影響, 即使沒有 `.env` 也會執行, 因此 `down -v` 同樣會刪除資料.
- MariaDB 只在第一次建立 volume 時套用 `MARIADB_*` 帳密. 之後若要變更, 需同時在資料庫中修改, 或重新建立 volume, 但這會刪除資料.
- 每次後端容器啟動時, 都會自動執行資料庫遷移.
- 後端會產生 4 個設備的模擬讀數, 每個設備每隔 `SIMULATOR_INTERVAL_SECONDS` 產生一筆 (預設為 1, 即每個設備每秒一筆). 此值在後端啟動時讀取, 修改後請重新啟動: `docker compose up -d backend`. 模擬讀數會保留在資料庫中, 沒有時間限制; 以預設速率計算, 每天約 345,000 筆.
- 監控畫面的即時視窗透過後端 WebSocket (`/api/v1/realtime/ws`) 接收讀數: Streamlit 服務端會為每位顯示即時視窗的使用者維持一條連線, 並每秒重繪. 沒有人查看的連線會在 30 秒後關閉. `GET /api/v1/realtime/window` 透過 REST 回傳相同的快照.
- 三個測試帳號各對應一種角色, 都來自 `.env`: `INITIAL_ADMIN_*` (Admin)、`INITIAL_USER_*` (User) 與 `INITIAL_VIEWER_*` (Viewer). 這六個變數都必須設定, 三個帳號名稱也不可相同. `.env.example` 的範例值僅供本機測試, 用於其他用途時請更改. 帳號不存在時, 會在後端啟動時建立. 帳號建立後不再套用這些值, 因此修改它們不會改變該帳號的密碼或角色. 不符合註冊規則的密碼仍可使用, 但後端會記錄 `initial_<role>_weak_password` 警告, 並列出缺少的條件. 請使用至少 8 個字元, 且包含大寫字母、小寫字母、數字與符號的密碼.
- 系統日誌 (Admin, `GET /api/v1/logs`) 會保存登入、登出、權限拒絕、角色變更、記錄變更、導入結果、範圍與單位變更, 以及系統錯誤, 但不保存模擬讀數. 超過 `LOG_RETENTION_DAYS` (預設 365) 的資料列會自動移除, 時機是後端啟動時, 之後最多每小時一次. 修改後請重新啟動後端: `docker compose up -d backend`.
- 每個系統日誌事件也會以 JSON 行的形式寫入後端的 stdout. 資料列約每秒批次寫入資料庫一次, 因此後端當機時, 資料庫可能遺失約最後一秒的資料列, 但 stdout 不受影響. 資料庫無法使用時, 資料列會暫存在記憶體中 (最多 10,000 筆, 超過時會捨棄最舊的資料並發出警告), 待資料庫恢復後再寫入.

### 連線限制

只有前端會對本機開放連接埠. 後端與資料庫都沒有對外的連接埠, 因此無法從本機或其他電腦直接連線 (包含 `127.0.0.1`), 請勿在 `compose.yaml` 中為它們加上 `ports`.

```text
瀏覽器 → 127.0.0.1:18501 → 前端 → backend:8000 → db:3306
                           └───── monitoring-net ─────┘
```

- 前端預設綁定 `127.0.0.1`, 只有本機可以開啟. 可透過 `.env` 的 `HOST_BIND_ADDRESS` 與 `FRONTEND_HOST_PORT` 修改. 這兩個設定不影響後端與資料庫.
- `e2e/` 的 `test_only_the_frontend_is_published_to_the_host` 會檢查 Compose 設定, 確認只有前端開放連接埠.

需要檢查後端或資料庫時, 請在 `monitoring-application/` 透過容器執行指令:

```sh
docker compose exec backend python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/api/v1/health/ready').status)"
```

```sh
docker compose exec db sh -c 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" "$MARIADB_DATABASE"'
```

## 角色

| 角色 | 權限 |
| --- | --- |
| Admin | 所有功能, 包含系統管理頁面. 可修改或刪除任何人的記錄 |
| User | 瀏覽、新增與批量導入. 只能修改或刪除自己建立的記錄 |
| Viewer | 瀏覽即時監控、資料列表與資料分析, 並下載 Excel 檔 |

- 在註冊頁建立的帳號預設為 Viewer. Admin 可在 系統管理 → 使用者管理 修改其角色.
- 同一帳號在別處登入時, 先前的登入會失效.
- 被標記為異常的記錄與其他記錄規則相同, 可由建立者或 Admin 修改或刪除. 修改數值或分類時會重新判斷是否異常, 改回範圍內時會移除異常標記.

## API 文件

[`docs/api-documentation.html`](docs/api-documentation.html) 是 API 文件的 Swagger UI 副本. 請下載後以瀏覽器開啟. 檢視時需要網路連線以載入 Swagger UI, 但不需要執行後端. 此副本已關閉 "Try it out". 後端不對 Docker 外部開放, 因此無法從本機直接發送請求. 所有端點都位於 `/api/v1/` 之下. WebSocket (`/api/v1/realtime/ws`) 未列於文件中, 因為 OpenAPI 不涵蓋 WebSocket.

[`docs/openapi.json`](docs/openapi.json) 是相同內容的 OpenAPI JSON 檔案, 可匯入 Postman、Swagger Editor 等工具.

修改 API 後, 請在此資料夾重新產生這兩個檔案:

```sh
docker build --target test -t monitoring-dashboard-backend-test backend
docker run --rm -v "$PWD/docs:/out" monitoring-dashboard-backend-test python -m scripts.export_api_docs /out/api-documentation.html --openapi-json /out/openapi.json
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

實際操作應用程式的瀏覽器測試位於 `e2e/`, 需要 `uv` 與 Google Chrome. 設定方式請見 [e2e/README.md](e2e/README.md).

### 前端單元測試

前端單元測試檢查 `frontend/core/` 中的邏輯, 不需要後端、資料庫或瀏覽器. 畫面的實際操作由 `e2e/` 的瀏覽器測試負責.

需要 `uv`. 在 `frontend/` 建立環境並執行:

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest tests
```

測試檔案位於 `frontend/tests/`:

* `test_records.py`
   * 檢查時間與數值的格式、範圍位置、記錄的操作權限、查詢參數, 以及列表換頁與載入失敗時的狀態.
* `test_record_form.py`
   * 檢查數值欄位的解析與錯誤訊息, 以及範圍與單位的說明文字.
* `test_live_feed.py`
   * 檢查 WebSocket 網址, 以及即時資料與心跳訊息如何更新畫面資料、異常列表、停滯狀態與時間視窗.
* `test_nav.py`
   * 檢查頁面登記、每個頁面所需的權限、公開頁面, 以及導覽列依權限隱藏頁面.
* `test_charts.py`
   * 檢查異常範圍的讀取, 以及圖表 Y 軸範圍在無資料、單一數值與超出範圍時的計算.
