# eyes/state

`vision_state.py` provides `LiveVisionState`, the thread-safe implementation of `VisionState`. It connects the eyes to `vision/grok/voice_tools.py`, the app and robotics. Per source, it holds the latest frame, marked frame, objects, payload and history, plus the last action.
