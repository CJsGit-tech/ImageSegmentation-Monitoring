# 已封存的眼睛實驗

這些實驗已不再繼續研究. 只有兩種方法保留在 `../selected/`:

- `17_dinov2_grabcut`: 與 YOLOE 的多邊形重疊度最高 (平均 IoU 0.66).
- `19_dinov2_heatmap_level`: 中心點與距離最接近 YOLOE (2.35 px 與 0.28 px).

眼睛數量以 3 張圖片上的 12 個貓狗眼睛為準, 比對對象是 Claude Code 估計、經人工複核的標記.
「與 YOLOE 比較」的分數只使用 YOLOE 偵測到的 9 個眼睛, 不使用已複核標記, 衡量的是與 YOLOE 的一致程度, 而非準確度.
參數是依這幾張圖片選定的, 因此這些結果並非最終證明.

YOLOE 基準: 命中 9 個眼睛, 0 個誤判.

## 漏判或誤判眼睛

| 實驗 | 命中眼睛 | 誤判眼睛 | 封存原因 |
|---|---|---|---|
| `01_threshold_components` | 11 | 1 | 漏判一個眼睛並多出一個誤判, 而局部峰值命中全部 12 個 |
| `03_difference_of_gaussians` | 8 | 4 | 命中的眼睛比 YOLOE 少 |
| `04_simple_blob_detector` | 10 | 2 | 有漏判也有誤判, 而局部峰值命中全部 12 個 |
| `06_grabcut` | 11 | 1 | 漏判一個眼睛並多出一個誤判, 偏移到 003 狗的鼻子 |
| `08_superpixels` | 11 | 1 | 漏判一個眼睛並多出一個誤判 |
| `09_gradient_voting` | 11 | 1 | 漏判一個眼睛並多出一個誤判, 圓形也貼合不佳 (外框 IoU 0.10) |
| `10_hough_ellipse` | 5 | 5 | 命中的眼睛比 YOLOE 少, 且有 2 個眼睛沒有形狀 |
| `11_mser` | 7 | 1 | 命中的眼睛比 YOLOE 少, 且有 4 個眼睛沒有形狀 |
| `12_canny_contours` | 3 | 0 | 命中的眼睛比 YOLOE 少, 且有 9 個眼睛沒有輪廓 |
| `15_dinov2_original_784` | 10 | 0 | 使用完整圖片, 漏判 2 個眼睛, 而裁切圖的分數較佳 |
| `15_dinov2_original_1568` | 10 | 1 | 使用完整圖片, 漏判 2 個眼睛並多出一個誤判, 而裁切圖的分數較佳 |
| `20_dinov2_threshold_components` | 11 | 0 | 漏判一個眼睛 |
| `24_dinov2_pixel_clustering` | 11 | 1 | 漏判一個眼睛並多出一個誤判, 輪廓也擴散到毛髮上 |

## DINO v1 由 DINOv2 取代

這些實驗命中全部 12 個眼睛且沒有誤判, 但相同方法已有 DINOv2 版本.

| 實驗 | 封存原因 |
|---|---|
| `02_local_peaks` | DINO v1 定位器, 沒有輪廓, 與已複核標記的距離誤差為 7.83 px |
| `05_guided_filter` | DINO v1, 由 `16_dinov2_guided_filter` 取代 |
| `07_watershed` | DINO v1, 由 `14_dinov2_peaks_watershed` 取代 |
| `13_heatmap_level` | DINO v1, 由 `19_dinov2_heatmap_level` 取代 |

## 未入選的 DINOv2 方法

這些實驗同樣命中全部 12 個眼睛且沒有誤判, 但在多邊形重疊度與 YOLOE 中心點、距離的接近程度上都未領先.

| 實驗 | 多邊形 IoU | 中心點偏移 | 距離差異 | 封存原因 |
|---|---|---|---|---|
| `14_dinov2_peaks_watershed` | 0.58 | 2.76 px | 4.18 px | 重疊度低於 GrabCut, 中心點與距離也和 YOLOE 相差較遠 |
| `16_dinov2_guided_filter` | 0.53 | 2.36 px | 0.52 px | 接近熱圖等高線, 但三項指標都落後 |
| `18_dinov2_superpixels` | 0.54 | 2.41 px | 0.91 px | 重疊度低於 GrabCut, 中心點與距離也落後熱圖等高線 |
| `21_dinov2_difference_of_gaussians` | 無 | 4.99 px | 5.29 px | 沒有輪廓, 且與 YOLOE 相差最遠 |
| `22_dinov2_blob_detector` | 無 | 2.97 px | 1.68 px | 沒有輪廓 |
| `23_dinov2_watershed_canny` | 0.56 | 3.34 px | 3.89 px | 重疊度低於 GrabCut, 中心點與距離也和 YOLOE 相差較遠 |

與已複核標記相比, `14_dinov2_peaks_watershed` 與 `23_dinov2_watershed_canny` 的距離誤差最低 (1.71 與 1.48 px).
它們仍被封存, 因為這次選擇是與 YOLOE 比較, 而且這些標記當時尚未經過審閱.
