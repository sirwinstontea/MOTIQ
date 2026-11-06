# grok/eval

Evaluates `select()` with and without the local shortcut.

| File | Purpose |
|---|---|
| `make_dataset.py` | Builds the small synthetic dataset in [`data/`](data) |
| `run_eval.py` | Runs the commands and writes a metrics report |

```bash
GROK_MOCK=1 python -m vision.grok.eval.run_eval
python -m vision.grok.eval.run_eval --data DIR --out FILE.md
```

With `GROK_MOCK=1` the numbers come from fake answers, so they test the harness and not the model. For a real result, replace `data/` with real photos in the same format.
