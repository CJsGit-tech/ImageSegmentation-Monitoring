# 圖像分割

## 專案介紹

本子專案使用含有兩隻 (含) 以上動物的 COCO 圖片, 分別描出每隻貓或狗及其眼睛的輪廓,
再量測每隻動物的雙眼距離, 以及兩隻動物之間的右眼距離.

本專案比較兩種方法, 兩者都不訓練模型:

- **YOLOE:** 以單一開放詞彙 (open-vocabulary) 分割模型同時描出動物與眼睛的輪廓.
- **YOLOE + DINOv2:** 由 YOLOE 描出動物輪廓, 再以 DINOv2 與 OpenCV 影像處理找出並描出眼睛輪廓.

方法、結果與限制請見 [submission README](submission/README.md).

## 技術棧

| 項目 | 技術 |
|---|---|
| 程式語言 | Python 3.13 |
| 分割模型 | YOLOE `yoloe-26l-seg`, 透過 Ultralytics 8.4.203 |
| 眼睛特徵 | DINOv2 ViT-S/14 (含 registers), 透過 PyTorch 2.15.0 |
| 影像處理 | OpenCV 5.0 (GrabCut、輪廓) 與 NumPy 2.5 |
| 設定檔 | YAML (`submission/scripts/config.yaml`) |
| 資料 | COCO val2017 圖片, 以及用於準確度檢查的 COCO 標註 |

腳本在可用時會透過 MPS 使用 Apple GPU, 否則使用 CPU.

**Docker、API 與測試帳號:** 本子專案為一組 Python 腳本, 因此沒有 Docker 設定、API 或使用者帳號.
這些屬於監控系統, 請見 [`../monitoring-application/`](../monitoring-application/README.md).

## 本地運行步驟

使用專案根目錄的 `.venv` (Python 3.13). 若要在相容的 Apple Silicon Mac 上重新建立, 請在專案根目錄執行:

```bash
python3.13 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r image-segmentation/submission/requirements.txt
```

參考: [Ultralytics 安裝指南](https://docs.ultralytics.com/quickstart/).

## 分割動物與眼睛

共有兩種流程. 兩者都以 YOLOE 處理動物, 差別在於如何找出眼睛.
請在專案根目錄執行指令.

### 流程一: YOLOE

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_test_samples.py
```

COCO 模型沒有眼睛類別, 因此使用 YOLOE, 它可依照文字提示分割任意物體.
提示內容為 `submission/scripts/config.yaml` 中的動物名稱與 "eye".
同一個模型會看每張圖兩次: 以 640 尺寸找動物, 以 1280 尺寸找眼睛,
因為在較大尺寸下動物容易漏掉或標錯類別. 只有位於偵測到的動物上的眼睛會被保留.

`submission/data/001-test_samples/` 中的每張圖片, 會在 `submission/outputs/001-test_samples_yoloe/`
產生一張標註圖 (動物為綠色, 眼睛為紅色) 與一個含信心分數的 JSON 檔.
所有設定皆在 `submission/scripts/config.yaml`.

第一次執行時會下載 YOLOE 權重與約 250 MB 的文字編碼器到 `model/` (不納入版本控制).

### 流程二: YOLOE + DINOv2

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_dinov2_test_samples.py
```

此流程只用 YOLOE 處理動物, 再將每隻動物裁切出來, 以 DINOv2 找出眼睛.
請先執行流程一, 因為此流程會讀取 `submission/outputs/001-test_samples_yoloe/` 中
YOLOE 的動物輪廓與眼睛輪廓.

```text
YOLOE 動物輪廓 → 依外框裁切動物 → DINOv2 區塊特徵
→ 與參考眼睛的相似度 (熱圖) → 動物輪廓內的峰值
→ 最多 2 個眼睛 → GrabCut 或熱圖等高線 → 眼睛輪廓
```

1. **裁切:** 依 YOLOE 的外框裁出每隻貓或狗. YOLOE 的輪廓會把搜尋範圍限制在該動物身上,
   因此同一裁切圖中其他動物的眼睛會被忽略.
2. **熱圖:** DINOv2 (ViT-S/14, 含 registers) 將裁切圖 (長邊 784 px) 轉為區塊特徵.
   每個區塊會與參考眼睛比較, 參考眼睛為其他測試圖中 YOLOE 眼睛輪廓下的 DINOv2 特徵.
3. **定位:** 動物身上最亮的峰值成為最多 2 個眼睛, 且只有在間距與分數看起來像一對眼睛時才保留.
4. **描輪廓:** 每個眼睛以兩種方式描出輪廓. GrabCut 依小視窗中的顏色把眼睛分離出來,
   熱圖等高線則在峰值一半高度處描出熱圖輪廓.

此流程不訓練模型. 結果存放在 `submission/outputs/001-test_samples_yoloe_dinov2/`,
包含裁切圖、熱圖, 以及每種描輪廓方式各一個資料夾. 只處理貓與狗.

第一次執行時會下載約 84 MB 的 DINOv2 權重到 `model/`.

### 其他樣本

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_and_dinov2_other_samples.py
```

在 `submission/data/002-other_samples/` 上執行兩種流程, 並以測試圖的 YOLOE 眼睛作為參考.
結果存放在 `submission/outputs/002-other_samples_yoloe/` 與
`submission/outputs/002-other_samples_yoloe_dinov2/`.

## 資料夾結構

```text
image-segmentation/
├── experiments/        先前的實驗, 不屬於最終流程
├── model/              模型權重 (不納入版本控制)
└── submission/         報告、資料集與最終流程
```

請從 [submission README](submission/README.md) 開始閱讀.
