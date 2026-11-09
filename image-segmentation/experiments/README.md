# 眼睛實驗

此資料夾存放先前的工作, 不屬於提交內容. 內容包含 DINO v1 與 DINOv2 實驗、熱圖比較、二值化熱圖與背景筆記.
[封存實驗](outputs/experiments/archive/README.md)說明嘗試過哪些方法及其結果.

| 位置 | 內容 |
|---|---|
| `scripts/` | 實驗、熱圖與二值化影像腳本 |
| `outputs/experiments/selected/` | 兩種入選方法的原始執行結果 |
| `outputs/experiments/archive/` | 其他實驗, 原因記錄在該資料夾的 README |
| `outputs/heatmaps/` | DINO v1 與 DINOv2 熱圖集 |
| `outputs/cropped_animals/` | 從範例圖片裁切的貓狗圖片 |
| `notes/` | YOLO11n-seg 筆記與圖表 |

這些腳本會使用 `../submission/` 中的 YOLOE 結果、資料集與共用程式碼. 請在專案根目錄執行, 例如:

```bash
.venv/bin/python image-segmentation/experiments/scripts/run_dinov2_experiments.py
```
