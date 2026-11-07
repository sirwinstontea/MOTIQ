# benchmark

Compares two ways of turning a demonstration video into tokens:

- **Method A, MOTIQ:** `VideoTokenizer` from `vision/planner`, without voice.
- **Method B, Groky:** the tokenizer in [`Groky_Planner_Agent`](../Groky_Planner_Agent).

The specification is in [`BENCHMARK_SPEC.md`](BENCHMARK_SPEC.md), and the status and findings are in [`HANDOFF.md`](HANDOFF.md). Both are in Italian.

| File | Purpose |
|---|---|
| `motiq_tokenize.py` | Method A with telemetry |
| `groky_tokenize.py` | Method B with telemetry |
| `export_groky_frames.py` | Exports method B frames as JPEG plus `index.json` |
| `cross_tokenize.py` | Crosses frame sets with prompt protocols on the same Grok model |
| `score.py` | Scores runs against ground truth and prints a summary table |
| `groky_voice_bench.py` | Runs the Groky pipeline with video and audio on PyBullet |
| `station_ur5e.rdk` | PyBullet station for the benchmark |

Videos in `videos/` and outputs in `results/` are not in the repository. Prepare them as the specification describes. Runs need a Grok key in `computer-vision/.env`, and `groky_voice_bench.py` also needs PyBullet.
