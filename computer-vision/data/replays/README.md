# data/replays

Recorded video for running the vision stack without a camera.

`sample_arm.mp4` is a static 2 second clip of the `arm` source. `demo_live.py` uses it in replay mode, and so does the CLI:

```bash
python -m vision.eyes.cli run --mode replay --file data/replays/sample_arm.mp4
```
