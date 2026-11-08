# ImageSegmentation-Monitoring

## 專案介紹

這是一個個人專案, 包含兩個子專案:

| 子專案 | 資料夾 | 內容 |
|---|---|---|
| **圖像分割與眼距量測** | [`image-segmentation/`](image-segmentation/README.md) | 在 COCO 圖片中分割貓、狗及其眼睛, 並量測每隻動物的雙眼距離, 以及兩隻動物之間的右眼距離 |
| **即時資料分析與監控系統** | [`monitoring-application/`](monitoring-application/README.md) | 由 FastAPI 後端、Streamlit 前端與 MariaDB 資料庫組成, 以 Docker Compose 部署 |

圖像分割的方法、結果與限制請見 [image-segmentation/submission/README.md](image-segmentation/submission/README.md).

## 技術棧

**圖像分割**

| 項目 | 技術 |
|---|---|
| 程式語言 | Python 3.13 |
| 動物與眼睛分割 | YOLOE (`yoloe-26l-seg`), 透過 Ultralytics 8.4 |
| 眼睛特徵 | DINOv2 ViT-S/14 (含 registers), 透過 PyTorch 2.15 |
| 影像處理 | OpenCV 5 (GrabCut、輪廓) 與 NumPy |
| 資料 | COCO val2017 圖片與標註 |

**監控系統**

| 項目 | 技術 |
|---|---|
| 程式語言 | Python 3.13 |
| 後端 | FastAPI (非同步 REST 與 WebSocket)、Pydantic、JWT (PyJWT) 與 Argon2 密碼雜湊, 以 Uvicorn 執行 |
| 資料庫 | MariaDB 11.7, 透過 SQLAlchemy 2 ORM 與 asyncmy 存取, 以 Alembic 管理遷移 |
| 前端 | Streamlit 1.67, 以 Altair 與 pandas 繪製圖表 |
| 檔案處理 | python-multipart (CSV／JSON 上傳)、XlsxWriter (Excel 匯出) |
| 部署 | Docker 多階段建置與 Docker Compose (資料庫、後端、前端三個容器) |
| 測試 | pytest (後端單元與整合測試), 以及使用 Playwright 的瀏覽器測試 |

各套件的確切版本請見 `monitoring-application/backend/requirements.txt` 與 `monitoring-application/frontend/requirements.txt`.

監控系統的主要功能:

- **帳號與權限:** 註冊、登入、登出; Admin、User、Viewer 三種角色.
- **資料管理:** 資料記錄的新增、分頁瀏覽、篩選、排序、修改與刪除, 以及 CSV／JSON 批量導入.
- **即時監控:** 系統每秒為 4 個模擬設備各產生一筆資料 (可在 `.env` 調整), 經 WebSocket 推送到前端, 折線圖與柱狀圖每秒更新, 超出範圍的數值標記為異常.
- **資料分析:** 依時間範圍與分類統計、各分類異常次數柱狀圖、趨勢圖、異常清單與 Excel 下載.
- **系統管理 (Admin):** 使用者與角色管理、系統日誌、資料庫狀態、即時資料歷史與異常範圍設定.

## 本地運行步驟

### 圖像分割

圖像分割程式直接以 Python 執行. 請在專案根目錄, 於 Apple Silicon Mac 或其他裝有 Python 3.13 的電腦上執行:

```bash
python3.13 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r image-segmentation/submission/requirements.txt
```

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_test_samples.py
```

第一次執行時會自動建立 `image-segmentation/model/`, 並下載 YOLOE 權重 (約 80 MB) 與文字編碼器 (約 250 MB) 到該資料夾.
之後的執行會直接使用已下載的模型.
所有腳本 (包含 YOLOE + DINOv2 與距離量測) 請見 [image-segmentation README](image-segmentation/README.md).

### 監控系統

監控系統以 Docker 在本地執行, 需要安裝 Git、Docker 與 Docker Compose v2. 從 GitHub 取得專案:

```bash
git clone https://github.com/CJsGit-tech/ImageSegmentation-Monitoring.git
```

```bash
cd ImageSegmentation-Monitoring/monitoring-application
```

接著依下一節的 Docker 指令啟動. 啟動後:

1. 以瀏覽器開啟 <http://127.0.0.1:18501>, 用「測試帳號資訊」中的任一帳號登入.
2. 以 User 或 Admin 到側欄「資料 → 批量導入」上傳 `docs/sample-records-1000.csv`, 即可看到 1000 筆範例資料.
3. 「即時監控」頁經 WebSocket 接收即時資料並每秒更新; 模擬資料在啟動後約 1 秒出現.

執行測試 (選用):

```bash
docker build --target test -t monitoring-dashboard-backend-test backend
```

```bash
docker run --rm monitoring-dashboard-backend-test
```

需要資料庫的整合測試請執行 `backend/tests/run_integration.sh`, 它會自動啟動並移除一個暫時的 MariaDB 容器. 瀏覽器測試的安裝與執行方式請見 [`monitoring-application/e2e/README.md`](monitoring-application/e2e/README.md).

## Docker 部署指令

監控系統以 Docker Compose 部署. 請在 `monitoring-application/` 中執行以下指令.

1. 建立環境變數檔, 並填入所有必填值 (資料庫密碼與 `JWT_SECRET_KEY`; 三個測試帳號已有範例值):

```bash
cp .env.example .env
```

2. 建置並啟動資料庫、後端與前端:

```bash
docker compose up -d --build
```

3. 確認三個服務皆為健康狀態:

```bash
docker compose ps
```

| 服務 | 預設位址 |
|---|---|
| 前端 | <http://127.0.0.1:18501> |
| 後端 API | <http://127.0.0.1:18000> |
| 資料庫 | 不對 Docker 外部公開 |

查看後端日誌:

```bash
docker compose logs -f backend
```

停止服務並保留資料庫:

```bash
docker compose down
```

`docker compose down -v` 會一併刪除資料庫 volume 與其中所有資料.

- **缺少設定值:** 每個 `docker compose` 指令都會讀取 `.env`, 若缺少必填值會停止並顯示缺少的變數名稱.
- **資料庫遷移:** 每次後端啟動時會自動執行.

圖像分割不使用 Docker.

## API 文件連結

後端執行時會提供自身的 API 文件:

| 文件 | 位址 |
|---|---|
| Swagger UI | <http://127.0.0.1:18000/docs> |
| ReDoc | <http://127.0.0.1:18000/redoc> |
| OpenAPI JSON | <http://127.0.0.1:18000/openapi.json> |

所有端點皆位於 `/api/v1/` 之下:

| 類別 | 端點 |
|---|---|
| 健康檢查 | `GET /health`、`GET /health/ready` |
| 使用者認證 | `POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /auth/me` |
| 資料記錄 | `GET /records` (分頁、篩選、排序)、`POST /records`、`GET /records/{id}`、`PATCH /records/{id}`、`DELETE /records/{id}`、`GET /records/categories`、`GET /records/anomaly-check` (儲存前檢查是否異常) |
| 批量導入 | `POST /records/imports` (CSV 或 JSON, 全部成功才寫入) |
| 分類設定 | `GET /category-settings`、`PATCH /category-settings` (Admin 設定異常範圍與單位)、`GET /category-display` |
| 即時監控 | `GET /realtime/window` (最近 5 分鐘)、`GET /realtime/range` (自訂時間範圍)、WebSocket `/realtime/ws` |
| 資料分析 | `GET /analytics/statistics`、`GET /analytics/trend`、`GET /analytics/anomalies`、`GET /analytics/export` (Excel) |
| 系統管理 (Admin) | `GET /users`、`PATCH /users/{id}/role`、`GET /logs`、`GET /logs/options`、`GET /db-status`、`GET /history/records` |

完整路徑、請求內容與回應格式請見 Swagger. 圖像分割為腳本形式, 沒有 API.

## 測試帳號資訊

監控系統有三種角色: **Admin**、**User** 與 **Viewer**. 三個測試帳號都在 `.env` 中設定, 後端每次啟動時會建立尚不存在的帳號. 若沿用 `.env.example` 的範例值, 可直接用以下帳號登入:

| 角色 | 帳號 | 密碼 | `.env` 變數 |
|---|---|---|---|
| **Admin** | `admin` | `Admin@12345` | `INITIAL_ADMIN_USERNAME`、`INITIAL_ADMIN_PASSWORD` |
| **User** | `user` | `User@12345` | `INITIAL_USER_USERNAME`、`INITIAL_USER_PASSWORD` |
| **Viewer** | `viewer` | `Viewer@12345` | `INITIAL_VIEWER_USERNAME`、`INITIAL_VIEWER_PASSWORD` |

- **必填:** 這六個變數都必須設定, 缺少時 `docker compose` 會停止並顯示缺少的變數名稱; 三個帳號名稱不可相同.
- **只用於測試:** 範例密碼是公開的, 部署到本機以外的環境前請在 `.env` 改成自己的值.
- **其他帳號:** 在註冊頁註冊的新帳號為 Viewer, 可由 Admin 在「系統管理 → 使用者管理」修改角色.

各角色可以做的事:

| 角色 | 權限 |
|---|---|
| **Admin** | 所有功能, 包含系統管理頁面; 可修改或刪除任何人的一般記錄 |
| **User** | 瀏覽、新增、批量導入; 只能修改或刪除自己建立的記錄 |
| **Viewer** | 只能瀏覽即時監控、資料列表與資料分析, 並下載 Excel |

- **密碼規則:** 至少 8 個字元, 需包含大寫字母、小寫字母、數字與符號.
- **帳號設定值:** 只在第一次建立帳號時套用, 之後修改 `.env` 不會改變已存在的帳號 (包含密碼與角色).
- **單一登入:** 同一帳號在別處登入時, 先前的登入會失效.
- **異常記錄:** 被標記為異常的記錄任何人都不能修改或刪除 (包含 Admin).

## 系統架構圖

![MonitoringDashboard 系統架構圖](monitoring-application/docs/system-architecture-modules.svg)

瀏覽器連到 Streamlit 前端容器; 前端以 REST 呼叫 FastAPI 後端容器, 並由 Streamlit 服務端以 WebSocket 接收即時資料, 經 JWT 認證後進入五個功能模組, 再透過 SQLAlchemy 存取 MariaDB. 模擬器是後端內的背景任務. 互動版本與各元件說明請見 [`system-architecture-modules.html`](monitoring-application/docs/system-architecture-modules.html) 與 [`system-architecture.html`](monitoring-application/docs/system-architecture.html) (下載後以瀏覽器開啟).

## 相關文件

| 文件 | 位置 |
|---|---|
| 環境變數範例 | [`monitoring-application/.env.example`](monitoring-application/.env.example) |
| 測試資料 CSV 範例 (1000 筆, 含 50 筆異常) | [`monitoring-application/docs/sample-records-1000.csv`](monitoring-application/docs/sample-records-1000.csv) |
| 測試資料 JSON 範例 | [`monitoring-application/docs/sample-records-1000.json`](monitoring-application/docs/sample-records-1000.json) |
| 系統架構圖 | [`monitoring-application/docs/system-architecture-modules.svg`](monitoring-application/docs/system-architecture-modules.svg) |
| 監控系統操作說明 (英文) | [`monitoring-application/README.md`](monitoring-application/README.md) |
| 瀏覽器測試說明 | [`monitoring-application/e2e/README.md`](monitoring-application/e2e/README.md) |

## 專案結構

```text
.
├── image-segmentation/          圖像分割與眼距量測
│   ├── submission/              報告、資料、腳本與結果
│   ├── experiments/             先前的實驗
│   └── model/                   模型權重 (不納入版本控制)
└── monitoring-application/      即時資料分析與監控系統
    ├── backend/                 FastAPI 程式、資料庫遷移與測試
    ├── frontend/                Streamlit 程式
    ├── e2e/                     瀏覽器測試
    ├── docs/                    範例資料與系統架構圖
    ├── compose.yaml
    └── .env.example
```
