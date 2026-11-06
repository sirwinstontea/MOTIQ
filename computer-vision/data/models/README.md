# data/models

Detector weights in ONNX format, tracked in Git.

| File | Size | Classes |
|---|---|---|
| `oggetti_mani.onnx` | 37 MB | 7 custom classes: the four demo objects plus arm, hand and wrist. Default for the live demo. |
| `yolov8n.onnx` | 13 MB | 80 COCO classes |

Custom weights come from [`training/`](../../training). Track IDs below 1000 come from YOLO. See [`vision/eyes`](../../vision/eyes).
