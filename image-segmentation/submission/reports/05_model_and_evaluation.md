# 5. AI 模型與評估

## 使用的模型

| 模型 | 用途 | 訓練 |
|---|---|---|
| **YOLOE** (`yoloe-26l-seg.pt`), 使用文字提示 | 兩種方法的動物輪廓, 以及 YOLOE 方法的眼睛輪廓 | 無 |
| **DINOv2** ViT-S/14, 含 registers | YOLOE + DINOv2 中眼睛熱圖的區塊特徵 | 無 |

GrabCut 與熱圖等高線是 YOLOE + DINOv2 的兩種影像處理方式, 不是訓練出來的模型.

## 為什麼選擇這些模型

**YOLOE**

1. **能尋找眼睛:** COCO 有動物類別但沒有眼睛類別, 因此以 COCO 訓練的模型無法描出眼睛. YOLOE 會依文字指示分割目標.
2. **不需要訓練:** 不需要標註的眼睛資料就能執行.
3. **動物偵測可靠:** 找到全部 7 隻樣本動物, 動物信心分數為 0.52-0.90.

**DINOv2**

1. **能找到 YOLOE 漏掉的眼睛:** 搭配局部峰值, 在範例圖片上找到全部 12 個標記的貓狗眼睛, YOLOE 則找到 9 個.
2. **不需要訓練:** 只需將其特徵與少數參考眼睛比較.
3. **優於另一個測試選項:** 原始 DINO 的熱圖雜訊較多. 使用相同步驟, 在相同 4 隻動物上, 與標記的距離誤差從 5.47 px 降到 1.71 px.

**GrabCut 與熱圖等高線**是從 25 次實驗中選出, 依據是 YOLOE 偵測到的 9 個樣本眼睛.
GrabCut 與 YOLOE 多邊形的重疊度最高, 熱圖等高線的中心點與距離最接近 YOLOE.
[實驗歷程](supporting/experiment_history.md)涵蓋完整過程, [YOLOE 筆記](supporting/yoloe_model.md)提供更多細節與引用來源.

## 評估公式

**交集比聯集 (Intersection over Union)**, 預測 $A$ 與參考 $B$ 的重疊程度:

$$IoU = \frac{|A \cap B|}{|A \cup B|}$$

**精確率、召回率與 F1**, 其中真陽性 (TP) 對應到真實物件, 假陽性 (FP) 沒有對應, 假陰性 (FN) 是漏掉的物件:

$$Precision = \frac{TP}{TP + FP} \qquad Recall = \frac{TP}{TP + FN} \qquad F1 = \frac{2 \cdot Precision \cdot Recall}{Precision + Recall}$$

**平均精確率 (Average Precision) 與 mAP**, COCO 風格的標準彙總指標:

$$AP = \int_0^1 P(R)\,dR \qquad mAP_{50\text{-}95} = \frac{1}{10}\sum_{t \in \{0.50,\,0.55,\,\ldots,\,0.95\}} AP_t$$

這裡沒有計算 AP 與 mAP, 因為眼睛參考是單一比對規則下的近似外框, 而且 DINOv2 方法沒有依信心分數排序的眼睛清單.

**距離誤差**, 其中 $d_{pred}$ 為量測距離, $d_{true}$ 為參考距離:

$$MAE = \frac{1}{n}\sum_i |d_{pred,i} - d_{true,i}| \qquad MAPE = \frac{100\%}{n}\sum_i \frac{|d_{pred,i} - d_{true,i}|}{d_{true,i}}$$

## 測試樣本的結果

比對對象是 12 個已複核標記的貓狗眼睛. 眼睛中心點落在標記內即算命中.
中心點誤差與外框 IoU 涵蓋 YOLOE 找到的 9 個眼睛, 兩種 YOLOE + DINOv2 方式也都命中這 9 個.

| 方法 | 命中 | 誤判 | 精確率 | 召回率 | F1 | 中心點誤差 | 外框 IoU |
|---|---|---|---|---|---|---|---|
| YOLOE | 9 | 0 | 100% | 75% | 0.86 | 1.98 px | 0.65 |
| YOLOE + DINOv2, GrabCut | 12 | 0 | 100% | 100% | 1.00 | 3.20 px | 0.69 |
| YOLOE + DINOv2, 熱圖等高線 | 12 | 0 | 100% | 100% | 1.00 | 2.50 px | 0.60 |

若包含鳥的標記眼睛, YOLOE 找到 13 個中的 9 個 (召回率 69.2%, F1 0.82).

## 與 YOLOE 的一致程度

這些分數只使用 YOLOE 偵測到的眼睛, 不使用已複核標記. 它們衡量的是與 YOLOE 的一致程度, 而非準確度.

| 方法 | 圖片 | 比較的眼睛 | 多邊形 IoU | 中心點偏移 | 距離差異 |
|---|---|---|---|---|---|
| YOLOE + DINOv2, GrabCut | 測試樣本 | 9 | 0.66 | 2.57 px | 3.00 px (4 隻動物) |
| YOLOE + DINOv2, 熱圖等高線 | 測試樣本 | 9 | 0.51 | 2.35 px | 0.28 px (4 隻動物) |
| YOLOE + DINOv2, GrabCut | 其他樣本 | 18 | 0.69 | 2.41 px | 2.50 px (7 隻動物) |
| YOLOE + DINOv2, 熱圖等高線 | 其他樣本 | 18 | 0.54 | 1.70 px | 0.54 px (7 隻動物) |

其他樣本包含 3 張測試樣本的副本, 它們佔比較對象中 18 個眼睛的 9 個, 以及 7 隻動物的 4 隻.
結果也收錄於 [`other_samples_method_comparison.csv`](files/measurements/other_samples_method_comparison.csv).

## 此評估的限制

- **標記來源:** 眼睛標記由 Claude Code 依放大檢視估計, 再經人工複核, 但沒有第二位獨立標註者.
- **其他樣本的準確度另外報告:** COCO 沒有動物眼睛標註, 因此其他樣本的分數使用已複核標記, 請見報告 7. YOLOE + DINOv2 輸出了 30 個沒有 YOLOE 對應的眼睛, 其中部分可能是誤判.
- **樣本少:** 只有 3 張圖片, 模型與參數也是依這些圖片選定, 因此分數可能偏樂觀.
- **命中規則寬鬆:** 中心點落在標記內就算命中, 即使輪廓不佳也一樣.
- **動物遮罩未計分:** 只計算了動物數量, 沒有計算與 COCO 遮罩的遮罩 IoU.
