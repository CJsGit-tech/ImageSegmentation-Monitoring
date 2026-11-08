# 圖像分割 & 實時監控平台

## 專案介紹

| 專案 | 資料夾 | 內容 |
|---|---|---|
| **圖像分割與眼距量測** | [`image-segmentation/`](image-segmentation/README.md) | 在 COCO 圖片中分割貓、狗及其眼睛, 並量測每隻動物的雙眼距離, 以及兩隻動物之間的右眼距離 |
| **即時資料分析與監控系統** | [`monitoring-application/`](monitoring-application/README.md) | 由 FastAPI 後端、Streamlit 前端與 MariaDB 資料庫組成, 以 Docker Compose 部署 |

各類專案的詳細結果與復現方法可在專案 ***README*** 中查閱.

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

## 圖像分割與眼距量測 （相關假設）

- 輸入資料:
    - 靜態的 COCO val2017 圖片, 每張至少有 2 隻動物. 此專案的量測對象為 **貓與狗**.

- 不微調模型: 
    - 預設是 PoC 專案, 目標放在探索開源專案中是否有可行的思路
    - COCO 沒有**眼睛類別**, 因此僅使用預訓練模型可導致準確率不足

- 眼睛歸屬:
    - 圖片中的動物皆以正面朝前.
    - 右眼定義:
        - 假設動物臉朝鏡頭且頭部直立因此取影像中 x 較小的眼睛
        - 側臉時可能判斷錯誤.

- 眼睛距離定義:
    - 兩個劃定的眼睛的圓圈之中心點, 使用歐氏距離, 單位為影像像素, 同時換算並推估公分(cm)
    - 量測條件
        - 一隻動物要有 2 個眼睛才量測雙眼距離並區分左右
        - 右眼距離需要至少 2 隻這樣的動物, 並計算每一對
        - 不校正頭部轉向與動物離鏡頭的遠近, 因此結果不等於實際的距離

- 驗證可行性
    - 準確度以 3 張測試圖的眼睛標記為基準. 參數也是依這 3 張圖調整的.
    - 有附上其他 9 張測試圖片, 當作 PoC 可行性之評估.

## 即時資料分析與監控系統 （相關假設）

- 資料模型:
    - 假設**應用場景為設備感測器讀數**
    -  一筆記錄代表某個分類在某個時間點的一次讀數. 
    - 以「分類」區分設備或指標, 沒有獨立的設備欄位, 可以自由設定新設備與讀數
    - 導入資料的時間戳需含時區資訊 (UTC 為主) 未填時以導入時間為準.
    - 系統日誌預設保留 365 天.

- 資料來源: 
    - 沒有串接真實設備, 由後端模擬 4 個設備 (溫度、壓力、馬達轉速、振動)
    - 預設每秒各產生一筆, 其中約 5% 刻意超出範圍以展示異常.
    - 歷史查詢單次最長 3 個月, 假設更長的期間可分段查詢.
        - 避免服務器負擔也預防大量的導出行為
    兒

- 異常判斷:
    - 數值小於等於最小值或大於等於最大值即為異常, 碰到邊界也算. 
    - 異常數據的最大／最小可以自行設定, 尚未設定範圍的分類不判斷.
        - **範圍由 Admin賬號依分類設定, 只影響之後的判斷, 已保存的記錄不回頭重判.**
    - 為了事後可追查, 異常記錄一律唯讀 (包含 Admin), 即使當初填錯也無法更正.
    
- 帳號管控：
    - 註冊不需核准, 新帳號一律為 Viewer, 由 Admin 調整權限. 

- 批量導入:
    - 檔案視為一個整體, 有任何一列錯誤就全部不寫入. 上限為 5 MB、10,000 列. 
    - 重複上傳只以檔名判斷, 不比對內容.


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

