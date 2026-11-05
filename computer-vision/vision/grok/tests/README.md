# grok/tests

Offline tests for `vision/grok`. `conftest.py` forces mock mode and sends the log to a temporary folder, so no key or network is needed.

| File | Covers |
|---|---|
| `test_grok_client.py` | Image encoding, retries, timeouts, invalid JSON, `json_object` fallback, logging |
| `test_grok_tasks.py` | The safety rule on invented IDs, no exceptions, mock answers, `select` |
| `test_grok_schemas.py` | Command and tracked object schemas, the group's input format |
| `test_grok_local_resolver.py` | Local shortcut resolution of commands |
| `test_grok_voice_tools.py` | Voice tool definitions, dispatch, context and errors |
| `test_grok_live.py` | YOLO-World to objects, GrabCut outline, reacquisition, with no models or network |
| `test_grok_cli.py` | CLI commands |
