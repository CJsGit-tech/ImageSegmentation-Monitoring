# 模型選擇: YOLOE 與 Ultralytics

本專案透過 Ultralytics Python SDK 使用 `yoloe-26l-seg.pt` 分割動物及其眼睛.

## 為什麼使用 Ultralytics SDK

Ultralytics 是 Python SDK, 讓物件偵測與實例分割模型容易使用. 同一個套件涵蓋本專案需要的步驟:

| 需求 | SDK 提供的功能 | 來源 |
|---|---|---|
| 預測 | 用幾行程式載入模型、設定文字提示並預測 | [YOLOE 文件][yoloe-docs] |
| 視覺化 | 以 `results[0].show()` 直接顯示結果 | [YOLOE 文件][yoloe-docs] |
| 在匯出模型上推論 | 直接在匯出的檔案上預測或驗證, 例如 `yolo26n.onnx` | [偵測文件][detect-docs] |
| 自訂訓練 | 準備標註資料集、調整設定檔, 並從 Python 訓練 | [Ultralytics 訓練指南][train-guide] |
| 微調 YOLOE | 專用的訓練器, 例如 `YOLOEPESegTrainer` | [YOLOE 文件][yoloe-docs] |
| 模型匯出 | ONNX、CoreML、NCNN、LiteRT 與其他格式 | [匯出文件][export-docs] |

依文件記載, YOLOE 本身可匯出為 ONNX、OpenVINO、TensorRT、CoreML、LiteRT 與 RKNN ([YOLOE 文件][yoloe-docs]).

## 為什麼使用 YOLOE

COCO 沒有眼睛類別, 因此以 COCO 訓練的模型無法分割眼睛. YOLOE 是開放詞彙模型: 它會依文字指示分割目標, 因此不需訓練就能要求尋找 `eye` ([YOLOE 文件][yoloe-docs]).

## 在邊緣裝置上的效能

**摘要:** 已發表的結果支持 YOLOE 在部分邊緣硬體上即時執行, 主要是較小的模型尺寸. 本專案目前的設定尚未在邊緣裝置上測試, 使用的模型也比下方邊緣結果中的模型大.

### 已發表的結果

[YOLOE 論文][yoloe-paper]在使用 TensorRT 的 Nvidia T4 GPU 與使用 CoreML 的 iPhone 12 上量測速度, 使用 LVIS 上的匯出模型:

| 模型 | Nvidia T4 (FPS) | iPhone 12 (FPS) |
|---|---|---|
| YOLOE-11-S | 301.2 | 73.3 |
| YOLOE-11-M | 168.3 | 39.2 |
| YOLOE-11-L | 130.5 | 35.1 |
| YOLOE-v8-S | 305.8 | 64.3 |
| YOLOE-v8-M | 156.7 | 41.7 |
| YOLOE-v8-L | 102.5 | 27.2 |
| YOLO-Worldv2-S (供比較) | 216.4 | 48.9 |
| YOLO-Worldv2-L (供比較) | 80.0 | 22.1 |

論文指出 YOLOE-v8-S 比 YOLO-Worldv2-S 在 T4 上快 1.4×, 在 iPhone 12 上快 1.3× ([論文][yoloe-paper]).

[YOLOE 文件][yoloe-docs]表示, `n` 與 `s` 尺寸可在 Jetson 等邊緣 GPU 上執行, 或在降低解析度後於 CPU 上執行. 文件也指出推論通常需要有 4–8 GB VRAM 的 NVIDIA GPU.

### 證據未能說明的部分

- **YOLOE-26 在裝置上的表現:** 論文早於 YOLOE-26, 因此上方數據是較舊的 YOLOE-v8 與 YOLOE-11 模型. 沒有找到 YOLOE-26 的裝置端速度.
- **Jetson 與 Raspberry Pi:** 沒有找到已發表的 YOLOE 速度量測.
- **獨立性:** 速度數據來自模型作者, 而非獨立的效能測試.
- **分割的額外負擔:** 文件提到載入的 `yoloe-26l-seg.pt` 比其偵測數據大, 為 35.4 M 參數與 142.0 B FLOPs ([YOLOE 文件][yoloe-docs]).

### 對本專案的影響

| 目前設定 | 對邊緣使用的影響 |
|---|---|
| 大型模型 (`26l`) | 文件建議邊緣 GPU 與 CPU 使用 `n` 與 `s` |
| 以 1280 尺寸搜尋眼睛 | 比 640 慢許多: 在我們於 Apple M4 Pro CPU 的粗略測試中, 每張約 0.8 秒對 0.22 秒 |
| 每張圖片推論兩次 | 模型執行次數約為單次推論的兩倍 |
| 文字編碼器約 254 MB | 匯出後可避免, 因為提示會儲存在匯出的模型中 ([YOLOE 文件][yoloe-docs]) |

改用較小的模型或輸入尺寸可能會降低眼睛準確度. 在我們的圖片上, conf 0.1 時, `26s` 找到 13 個眼睛中的 7 個, `26l` 則為 9 個; 尺寸 640 找到 5 個, 1280 則為 9 個.
邊緣部署應在目標裝置上測試.

匯出的 YOLOE 模型是靜態的: `set_classes()` 與視覺提示在匯出後無法使用, 因此必須先選定提示 ([YOLOE 文件][yoloe-docs]).

## 目前方法的限制

### 只測試了特定動物

設定檔提示 `bird`、`cat` 與 `dog`, 測試也只用了含有這些動物的 3 張圖片. COCO 的其他動物類別 (馬、羊、牛、大象、熊、斑馬、長頸鹿) 尚未測試, 因此結果不一定適用.

### 眼睛信心分數低

| 偵測對象 | 範例圖片上的信心分數 |
|---|---|
| 動物 | 0.52–0.90 |
| 眼睛 | 0.12–0.35 |

因此 `eye_conf` 必須維持在較低的 0.1. 提高到 0.25 會失去大部分眼睛. 13 個標記眼睛中仍有 4 個被漏掉, 都在圖片 001.

### 建議的下一步: 自訂訓練

建立自己的訓練資料集並進行自訂訓練. 這是 SDK 適合本專案的主要原因之一, 因為用於預測的同一個套件也涵蓋訓練、驗證與匯出.

1. **建立資料集.** [Pascal-Part][pascal-part] 已包含貓、狗、牛、馬與羊的眼睛部位遮罩, 相關的 [Pascal-Part-116][pascal-part-116] 基準也列出鳥眼. 因為 COCO 沒有眼睛遮罩, 可以手動標註 COCO 圖片以符合本專案.
2. **訓練.** 訓練帶有 `eye` 類別的 YOLO 分割模型 ([訓練指南][train-guide]), 或微調 YOLOE ([YOLOE 文件][yoloe-docs]).
3. **評估.** 保留獨立的測試集, 並回報遮罩 mAP、精確率與召回率.
4. **匯出.** 匯出為適合目標裝置的格式 ([匯出文件][export-docs]).

預期情況:

- **小眼睛依然困難:** 微調後[小物件仍可能被漏掉][roboflow], 訓練也無法為只有幾個像素寬的眼睛增加細節.
- **微調後類別固定:** 以 `YOLOEPESegTrainer` 微調的檢查點在類別變更時會報錯 ([YOLOE 文件][yoloe-docs]).
- **授權:** [YOLOE 文件][yoloe-docs]中維護者的留言表示, 正式環境使用需遵守 AGPL-3.0 或取得企業授權. 商業使用前請先確認.

### 以視覺語言模型減少標註工作

手動標註眼睛很繁瑣, 因此視覺語言模型 (VLM) 可以產生初步標籤供人修正. 我們曾使用 GPT 等近期模型協助標註文件版面, 相同的想法也可應用於此.

這個方法有已發表研究支持. 一篇 [2025 年的論文][auto-label-paper]使用視覺語言模型產生標籤, 再以這些標籤訓練輕量偵測器, 報告了具競爭力的效能, 同時大幅減少標註時間與成本. 它使用的是 YOLO-World、YOLOE 與 Grounding DINO, 而非 GPT 或 Gemini.

**建議流程:**

1. **產生初步外框.** 請 VLM 為每張動物裁切圖提供眼睛外框. 例如 [Gemini API][gemini-docs] 可回傳正規化座標的外框, 需要再換算為像素.
2. **產生遮罩.** 將每個外框交給 SAM 產生眼睛遮罩. SAM 接受外框提示, Ultralytics 的 [`auto_annotate`][auto-annotate] 也已將偵測器與 SAM 串接, 並寫出 YOLO 格式的多邊形標籤, 但其偵測器必須是具有該類別的 YOLO 模型.
3. **審閱.** 請人修正或退回每個標籤.
4. **訓練.** 使用 Ultralytics SDK 以審閱後的標籤訓練.

**需要管理的風險:**

- **眼睛比文件版面更難:** 版面區域很大, 而這裡有些眼睛只有幾個像素寬. 沒有找到 GPT 或 Gemini 在小物件上外框準確度的對照比較, 因此應先在少量手動標註的樣本上測試.
- **錯誤標籤會教壞模型:** 自動標籤可能[加入錯誤標籤][auto-label-review], 尤其是困難物件或低信心時, 這正是第 3 步重要的原因.
- **測試要保持獨立:** 最終測試集必須由人標註與檢查, 不可取自 VLM 輸出. 否則分數只顯示與 VLM 的一致程度.
- **分割支援不一:** Gemini 的[影像理解文件][gemini-docs]描述了 Gemini 2.5 起的輪廓遮罩, 但 [Gemini 3 指南][gemini-3]表示 Gemini 3 Pro 與 Flash 不支援. 使用 SAM 產生遮罩可避免依賴這一點.

## 參考資料

- [YOLOE: Real-Time Seeing Anything (論文)][yoloe-paper]
- [Ultralytics YOLOE 文件][yoloe-docs]
- [Ultralytics 模型匯出文件][export-docs]
- [Ultralytics 物件偵測文件][detect-docs]
- [Ultralytics 實例分割自訂訓練指南][train-guide]
- [Roboflow: 以自訂資料訓練 YOLO26 分割模型][roboflow]
- [Going Denser with Open-Vocabulary Part Segmentation (Pascal-Part)][pascal-part]
- [Understanding Multi-Granularity for Open-Vocabulary Part Segmentation (Pascal-Part-116)][pascal-part-116]
- [Auto-Labeling Data for Object Detection (論文)][auto-label-paper]
- [Auto-Labeling Data for Object Detection 文獻評論][auto-label-review]
- [Gemini API 影像理解文件][gemini-docs]
- [Gemini 3 開發者指南][gemini-3]
- [Ultralytics SAM 文件, 包含自動標註][auto-annotate]

[yoloe-paper]: https://arxiv.org/abs/2503.07465
[yoloe-docs]: https://docs.ultralytics.com/models/yoloe
[export-docs]: https://docs.ultralytics.com/modes/export
[detect-docs]: https://docs.ultralytics.com/tasks/detect
[train-guide]: https://www.ultralytics.com/blog/how-to-custom-train-ultralytics-yolo26-for-instance-segmentation
[roboflow]: https://blog.roboflow.com/train-yolo26-instance-segmentation-custom-data/
[pascal-part]: https://arxiv.org/pdf/2305.11173
[pascal-part-116]: https://arxiv.org/pdf/2406.11384
[auto-label-paper]: https://arxiv.org/pdf/2506.02359
[auto-label-review]: https://www.themoonlight.io/review/auto-labeling-data-for-object-detection
[gemini-docs]: https://ai.google.dev/gemini-api/docs/image-understanding
[gemini-3]: https://ai.google.dev/gemini-api/docs/gemini-3
[auto-annotate]: https://docs.ultralytics.com/models/sam
