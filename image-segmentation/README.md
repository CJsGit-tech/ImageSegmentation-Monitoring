# 圖像分割

本專案比較兩種方法, 兩者都不進行模型微調:

- **YOLOE:** 以開放詞彙 (open-vocabulary) 分割模型, 透過提示詞 `eye` 與動物名稱描出動物與眼睛的輪廓.
   - 結論:
      - 由於眼睛在影像中所佔的像素較少, 即使將影像放大再進行預測, 仍有少數眼睛未被偵測到且眼睛的信心分數偏低.

- **YOLOE + DINOv2:** 先由 YOLOE 找出動物並進行圖像切割, 送入 DINOv2 來幫助定位眼睛特徵, 最後再以 OpenCV 影像處理描出眼睛輪廓.
   - 結論:
      - DINOv2 的 ViT (Vision Transformer) 架構搭配 YOLOE 儲存的眼睛輪廓進行疊加圖片區塊網格上, 接著比較每個區塊與參考眼睛的特徵相似度, 產生熱圖以定位眼睛.
      - 在測試樣本的 12 個標記眼睛中命中全部 12 個且沒有誤判, 
         - 純 YOLOE 則命中 9 個. 在 9 張新 COCO 圖片上, 眼睛召回率僅 `22.6%`. 但搭配 DINOv2 後提升至 `74.2%`
            - 但精確率由 `77.8% 降至 71.9%`
            - 速度也由 `5.67 FPS 降至 1.97-4.00 FPS` (每次執行略有差異)

![參考眼睛向量的建立過程: DINOv2 區塊向量、YOLOE 眼睛輪廓、保留眼睛區塊, 再平均為參考向量](submission/data/figures/dinov2_reference_vector.gif)

## 資料夾結構 & 內容

```text
image-segmentation/
├── experiments/        先前的實驗, 不屬於最終流程
├── model/              模型權重 (不納入版本控制)
└── submission/         解答、資料集與最終流程
```

```text
submission/
├── QuestionsSubmission.md          問題一的解答
├── requirements.txt                已安裝的 Python 套件版本, 含單元測試用的套件
├── data/
│   ├── 001-test_samples/           3 張測試樣本
│   ├── 002-other_samples/          9 張其他 COCO 圖片、測試樣本副本與 eye_marks.json (全部 12 張的眼睛標記)
│   └── figures/                    解答與 README 使用的圖片與動畫
├── outputs/
│   ├── 001-test_samples_yoloe/            YOLOE 結果
│   ├── 001-test_samples_yoloe_dinov2/     裁切圖、熱圖與兩種輪廓結果
│   ├── 002-other_samples_yoloe/           YOLOE 結果
│   ├── 002-other_samples_yoloe_dinov2/    熱圖、兩種輪廓結果與 summary.csv
│   ├── 002-other_samples_performance/     速度與準確度結果
│   ├── 004-labeled_eye_centers/           全部 12 張的眼睛標記框、中心點與距離
│   └── measurements/                      距離量測與驗證結果
├── tests/                          單元測試
└── scripts/
    ├── config.yaml                                  資料、輸出、YOLOE 與量測設定
    ├── run_yoloe_test_samples.py                    在測試樣本上執行 YOLOE
    ├── run_yoloe_dinov2_test_samples.py             在測試樣本上執行 YOLOE + DINOv2
    ├── run_yoloe_and_dinov2_other_samples.py        在其他樣本上執行 YOLOE 與 YOLOE + DINOv2
    ├── evaluate_yoloe_and_dinov2_other_samples.py   其他樣本的速度與準確度
    ├── dinov2_eye_pipeline.py                       YOLOE + DINOv2 的共用步驟
    ├── measure_yoloe_eyes.py                        單一 YOLOE 結果的 R2 與 R3
    ├── measure_yoloe_dinov2_eyes.py                 單一 YOLOE + DINOv2 結果的 R2 與 R3
    ├── annotate_labeled_eye_centers.py              畫出眼睛標記框、中心點與距離
    └── export_submission_files.py                   所有結果的 R2 與 R3 及驗證摘要輸出到 outputs/measurements/, 圖表輸出到 data/figures/
```

## 技術棧

| 項目 | 技術 |
|---|---|
| 程式語言 | Python 3.13 |
| 分割模型 | YOLOE `yoloe-26l-seg`, 透過 Ultralytics 8.4.203 |
| 眼睛特徵 | DINOv2 ViT-S/14 (含 registers), 透過 PyTorch 2.15.0 |
| 影像處理 | OpenCV 5.0 (GrabCut、輪廓) 與 NumPy 2.5 |
| 設定檔 | YAML (`submission/scripts/config.yaml`) |
| 資料 | COCO val2017 圖片, 以及用於準確度檢查的 COCO 標註 |


## 本地運行步驟

本子專案直接以 Python 執行, 不使用 Docker. 請在專案根目錄, 於 Apple Silicon Mac 或其他裝有 Python 3.13 的電腦上建立 `.venv` 並安裝套件:

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

### 模型下載

第一次執行時會自動建立 `model/` 資料夾 (不納入版本控制) 並下載所需模型, 之後的執行會直接使用該資料夾中的模型.

| 模型 | 檔案 | 用途 | 下載時機 | 大小 |
|---|---|---|---|---|
| YOLOE | `yoloe-26l-seg.pt` | 分割動物與眼睛 | 流程一 | 約 80 MB |
| MobileCLIP 文字編碼器 | `mobileclip2_b.ts` | 將提示詞轉為文字特徵 | 流程一 | 約 250 MB |
| DINOv2 ViT-S/14 (含 registers) | `dinov2_vits14_reg4_pretrain.pth` | 產生區塊特徵以定位眼睛 | 流程二 | 約 88 MB |

### 流程一: YOLOE

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_test_samples.py
```

COCO 模型沒有眼睛類別, 因此使用 YOLOE, 它可依照文字提示分割任意物體.
   - 所有設定皆在 `submission/scripts/config.yaml`.
      - 提示內容為 `submission/scripts/config.yaml` 中的動物名稱與 "eye_prompt".
   
   - 同一個模型會看每張圖兩次: 以 640 尺寸找動物, 以 1280 尺寸找眼睛
   
   - `submission/data/001-test_samples/` 中的每張圖片, 會在 `submission/outputs/001-test_samples_yoloe/` 產生一張標註圖 (動物為綠色, 眼睛為紅色) 與一個含信心分數的 JSON 檔.


### 流程二: YOLOE + DINOv2

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_dinov2_test_samples.py
```

此流程只用 YOLOE 處理動物, 再將每隻動物裁切出來 最後以 DINOv2 找出眼睛.

**請先執行流程一, 因為此流程會讀取 `submission/outputs/001-test_samples_yoloe/` 中 YOLOE 的動物輪廓與眼睛輪廓.**


```text
腳本執行流程
YOLOE 動物輪廓 → 依外框裁切動物 → DINOv2 區塊特徵 → 參考眼睛的相似度 (熱圖) → 動物輪廓內的峰值 → 最多 2 個眼睛 → OpenCV做圖像後置畫出眼睛輪廓
```

此流程不訓練模型. 結果存放在 `submission/outputs/001-test_samples_yoloe_dinov2/`, 包含裁切圖、熱圖, 以及每種描輪廓方式各一個資料夾.



### 其他樣本

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_and_dinov2_other_samples.py
```
在 `submission/data/002-other_samples/` 上執行兩種流程, 並以測試圖的 YOLOE 眼睛作為參考.

結果存放在
   - `submission/outputs/002-other_samples_yoloe/` 
   - `submission/outputs/002-other_samples_yoloe_dinov2/`



## 動物與眼睛分割及距離量測

### 結果摘要

**測試樣本:** 3 張圖片, 含 12 個已複核標記的貓狗眼睛.

| 方法 | 命中眼睛 (共 12 個) | 誤判眼睛 | 雙眼距離 MAE, 相同 4 隻動物 | 右眼距離 MAE |
|---|---|---|---|---|
| YOLOE | 9 | 0 | 2.52 px | 1.82 px (2 對) |
| YOLOE + DINOv2, GrabCut | 12 | 0 | 4.31 px | 2.92 px (3 對) |
| YOLOE + DINOv2, HeatMap | 12 | 0 | 2.67 px | 2.09 px (3 對) |

### 其他樣本

| 方法 | 輸出眼睛 | 有兩個眼睛的動物 (共 25 隻) | 右眼配對 |
|---|---|---|---|
| YOLOE | 18 | 7 | 2 |
| YOLOE + DINOv2, 任一方式 | 48 | 23 | 15 |

### 其他數據

**其他樣本中 9 張新 COCO 圖片的速度與準確度:**
| 方法 | FPS | 眼睛精確率 | 眼睛召回率 | 眼睛 F1 | 雙眼距離 MAE |
|---|---|---|---|---|---|
| YOLOE | 5.67 | 77.8% | 22.6% | 0.35 | 1.95 px (2 隻動物) |
| YOLOE + DINOv2, GrabCut | 1.97 | 71.9% | 74.2% | 0.73 | 1.71 px (8 隻動物) |
| YOLOE + DINOv2, HeatMap | 4.00 | 71.9% | 74.2% | 0.73 | 2.17 px (9 隻動物) |

### 觀察結論
1. 雖然`方法２`在眼睛的定位上更加準確, 但在輪廓繪製上不如 YOLOE 的模型精準

2. 雖然`方法２`在眼睛的定位上更加準確, 但也製造了更多 False Positives


## 方法論的一些限制

1. YOLOE 最主要的問題在於小物件上的精準度不高
   - 漏掉許多眼睛
   - 眼睛信心分數低: 測試樣本上的眼睛信心分數只有 0.12-0.35, 因此門檻必須維持在 0.1

2. YOLOE + DINOv2
   - 無法回報「沒有眼睛」導致誤判
   - 推論速度大幅變慢


## 關於量測眼睛距離

有兩支腳本會印出單張圖片的計算過程, 兩種方法各一支. 

模式由 `scripts/config.yaml` 的 `both_eyes` 設定
   - `both-eyes` 量測每隻動物的雙眼 (R2), 
   - `right-eye-pair` 量測每一對動物的右眼 (R3).


YOLOE:

```bash
.venv/bin/python image-segmentation/submission/scripts/measure_yoloe_eyes.py --json image-segmentation/submission/outputs/001-test_samples_yoloe/002-coco-sample.json
```

YOLOE + DINOv2, 使用 `grabcut/` 或 `heatmap_level/` 資料夾中的 JSON 檔:

```bash
.venv/bin/python image-segmentation/submission/scripts/measure_yoloe_dinov2_eyes.py --json image-segmentation/submission/outputs/001-test_samples_yoloe_dinov2/grabcut/002-coco-sample.json
```

兩者都適用於任一組圖片. 若要一次量測所有結果, 並將距離量測與驗證摘要的 CSV 檔儲存到 `submission/outputs/measurements/`, 請依下方說明執行 `export_submission_files.py`.

## 眼睛標記

全部 12 張圖片的眼睛標記都存放在 `submission/data/002-other_samples/eye_marks.json`. 標記由 Claude Code 估計, 再經人工複核.

| 欄位 | 內容 |
|---|---|
| `status` | 標記狀態的說明文字 |
| `format` | 標記框格式的說明文字 |
| `images` | 以圖片檔名 (不含副檔名) 為鍵, 列出該圖的動物 |
| `images.<圖片>[].animal` | 動物的描述, 例如 `tabby cat` |
| `images.<圖片>[].eyes` | 眼睛標記框 `[x1, y1, x2, y2]`, 單位為原圖像素, 可能有 0-2 個 |
| `images.<圖片>[].note` | 選填備註, 例如 `Eyes closed` |
| `ignore` | 以圖片檔名為鍵的忽略框 `[x1, y1, x2, y2]`, 預測眼睛的中心點落在框內時不計分 |
| `ignore_reasons` | 各圖片使用忽略框的原因 |

眼睛中心點為標記框的中心 $((x_1 + x_2) / 2, (y_1 + y_2) / 2)$, 兩個中心點的距離即為驗證距離量測時使用的標記值.

畫出標記框、中心點與距離, 結果存放在 `submission/outputs/004-labeled_eye_centers/`:

```bash
.venv/bin/python image-segmentation/submission/scripts/annotate_labeled_eye_centers.py
```

## 單元測試

單元測試使用小型的人工輸入, 不需要模型權重、GPU 或資料集. 它們檢查程式邏輯, 模型的準確度則由其他樣本的評估腳本負責.

安裝 `requirements.txt` 後, 請在專案根目錄執行:

```bash
.venv/bin/python -m pytest image-segmentation/submission/tests
```

測試檔案位於 `submission/tests/`:

* `test_measure_yoloe_eyes.py`
   * 檢查眼睛中心點與距離的計算、每隻動物只保留信心最高的 2 個眼睛, 以及雙眼距離與右眼距離的量測與略過規則.
* `test_dinov2_eye_pipeline.py`
   * 檢查熱圖峰值的尋找、雙眼的間距與分數規則、參考眼睛向量的建立, 以及裁切座標的換算.
* `test_run_yoloe_test_samples.py`
   * 檢查重複偵測的移除, 以及如何判斷眼睛屬於哪一隻動物.

## 重現結果

請在專案根目錄依序執行:

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_test_samples.py
```

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_dinov2_test_samples.py
```

```bash
.venv/bin/python image-segmentation/submission/scripts/run_yoloe_and_dinov2_other_samples.py
```

```bash
.venv/bin/python image-segmentation/submission/scripts/export_submission_files.py
```

```bash
.venv/bin/python image-segmentation/submission/scripts/evaluate_yoloe_and_dinov2_other_samples.py
```

最後一個指令需要 `experiments/data/coco_annotations/` 中的 COCO 標註檔.

在新環境中安裝套件:

```bash
.venv/bin/python -m pip install -r image-segmentation/submission/requirements.txt
```
