# eyes/tracking

Detection and stable IDs. `tracker.py` provides `ObjectTracker`, which uses `ultralytics.YOLO` with ByteTrack when available. `SimpleByteTracker` is the lightweight IoU tracker used as a fallback. Each object also gets a dominant colour name and a palette colour for its label.

ID ranges are defined across the system: below 1000 YOLO, 1000 and up Grok, 2000 and up YOLO-World.
