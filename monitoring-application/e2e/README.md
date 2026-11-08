# 瀏覽器測試

這些 Playwright 測試會像真人一樣, 在 Chrome 中操作實際的應用程式. 涵蓋範圍如下, 之後的每個批次都會在此新增自己的測試:

- **Batch 1:** 註冊、登入與登出、保持登入、依角色顯示的頁面
- **Batch 2:** 新增、列表、編輯、刪除、異常警告與鎖定、範圍設定
- **Batch 3:** CSV 與 JSON 批量導入
- **Batch 4:** 模擬資料、即時監控畫面及其自訂時間範圍
- **Batch 5:** 統計、趨勢、異常列表與 Excel 匯出
- **Batch 6:** 使用者角色、系統日誌、資料庫狀態與系統資料歷史
- **Batch 7:** `.env` 測試帳號、每秒一筆的模擬器、由 WebSocket 提供資料的即時視窗與異常數量長條圖

## 需要的環境

- 在本機執行的服務: 在 `monitoring-application/` 執行 `docker compose up -d`.
- 已安裝 Google Chrome. 測試直接使用它, 因此 Playwright 不需要下載瀏覽器. 若要改用 Playwright 自己的 Chromium, 請執行一次 `.venv/bin/playwright install chromium`, 並將 `E2E_BROWSER_CHANNEL=` 設為空值.
- 能以 Docker 存取這些服務. 應用程式目前沒有變更角色的 API, 因此測試會透過後端容器內的 `db_tool.py` 設定角色.

## 初次設定

在 `monitoring-application/e2e/` 執行:

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

## 執行

```sh
.venv/bin/python -m pytest
```

常用選項:

- `--headed --slowmo 300`: 觀看瀏覽器操作.
- `-k edit`: 執行名稱包含 `edit` 的測試.
- `--screenshot only-on-failure --output <folder>`: 保留每個失敗的截圖.

以下設定皆為選填:

| 變數 | 預設值 | 用途 |
| --- | --- | --- |
| `E2E_BASE_URL` | `http://127.0.0.1:18501` | 前端位址 |
| `E2E_API_URL` | `http://127.0.0.1:18000` | 後端位址 |
| `E2E_ENV_FILE` | 無, 由 Compose 讀取 `.env` | 啟動服務時使用的環境變數檔 |
| `E2E_BROWSER_CHANNEL` | `chrome` | 使用的瀏覽器; 空值代表 Playwright 的 Chromium |
| `E2E_KEEP_DATA` | 未設定 | 設為 `1` 時保留該次執行的帳號與記錄以供檢查 |

## 資料

每次執行都會註冊自己的帳號, 並在自己的分類中建立記錄, 名稱都帶有隨機的 `e2e-<id>` 前綴 (分類以 `!e2e-<id>` 開頭, 因此會排在最前面). 結束時會刪除所有帶此前綴的資料, 包含異常記錄與導入記錄, 這是應用程式本身不允許的操作. `db_tool.py` 會拒絕刪除前綴不是 `e2e-` 開頭的任何資料.

請只對本機或測試環境執行這些測試. 它們會寫入服務所使用的資料庫.

## 未涵蓋的範圍

- 儲存或導入逾時後重試: 這需要 30 秒的停滯. 後端部分由 `test_retrying_a_save_with_the_same_key_adds_one_record` 與後端測試檢查.
- 後端中斷、手機寬度、螢幕閱讀器與鍵盤焦點.
- 監控畫面的暫停、重新連線與首次載入狀態, 以及圖表本身的繪製.
- 即時更新測試會等待模擬器的下一筆讀數, 最長為其間隔 (預設 1 秒) 加 15 秒.
- WebSocket 測試檢查前端容器與後端保持開啟的連線, 以及畫面約每秒變化一次, 但不讀取 WebSocket 訊框本身.
- Excel 檔只檢查原始文字 (標題與單位), 不逐格檢查; 後端測試會以 openpyxl 讀回檔案.
- 所有視覺呈現: 列表的異常標記與範圍條繪製在這些測試無法讀取的 canvas 上, 因此改為檢查操作列與 API.
