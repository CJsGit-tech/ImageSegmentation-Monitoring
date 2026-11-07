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
| 後端 | FastAPI、Pydantic、JWT (PyJWT) 與 Argon2 密碼雜湊, 以 Uvicorn 執行 |
| 資料庫 | MariaDB 11.7, 透過 SQLAlchemy 2 ORM 與 asyncmy 存取, 以 Alembic 管理遷移 |
| 前端 | Streamlit, 以 Altair 與 pandas 繪製圖表 |
| 部署 | Docker 多階段建置與 Docker Compose |
| 測試 | pytest, 以及使用 Playwright 的瀏覽器測試 |

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

監控系統以 Docker 在本地執行, 步驟如下一節所述. 需要安裝 Docker 與 Docker Compose v2.

## Docker 部署指令

監控系統以 Docker Compose 部署. 請在 `monitoring-application/` 中執行以下指令.

1. 建立環境變數檔, 並填入所有必填值:

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
| 資料記錄 | 於 `/records` 下新增、分頁瀏覽 (含篩選與排序)、讀取、更新與刪除 |
| 批量導入 | CSV 與 JSON 批量導入 |
| 分類設定 | 異常範圍設定與顯示範圍 |

完整路徑、請求內容與回應格式請見 Swagger. 圖像分割為腳本形式, 沒有 API.

## 測試帳號資訊

監控系統有三種角色: **Admin**、**User** 與 **Viewer**.

| 帳號 | 取得方式 |
|---|---|
| **Admin** | 後端啟動時, 若該帳號尚不存在, 會依 `monitoring-application/.env` 中的 `INITIAL_ADMIN_USERNAME` 與 `INITIAL_ADMIN_PASSWORD` 建立 |
| **Viewer** | 在前端的註冊頁面或透過 `POST /api/v1/auth/register` 註冊. 新帳號預設為 Viewer |

- **密碼規則:** 至少 8 個字元, 需包含大寫字母、小寫字母、數字與符號.
- **Admin 設定值:** 只在第一次建立帳號時套用, 之後修改 `.env` 不會改變已存在的帳號.
- **User 角色:** 目前 API 尚無變更角色的端點, 因此無法透過系統建立 User 帳號.
  瀏覽器測試是以 `monitoring-application/e2e/db_tool.py` 直接在資料庫中設定角色.

<!-- 請在此填入要提供給審閱者的示範帳號資訊, 不要放入真正的密鑰. -->

## 相關文件

| 文件 | 位置 |
|---|---|
| 環境變數範例 | [`monitoring-application/.env.example`](monitoring-application/.env.example) |
| 測試資料 CSV 範例 | [`monitoring-application/docs/sample-records-1000.csv`](monitoring-application/docs/sample-records-1000.csv) |
| 系統架構圖 | [`monitoring-application/docs/system-architecture.html`](monitoring-application/docs/system-architecture.html) |

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
