# examples

Task JSON files for the controller, following the [action contract](../docs/AI_ACTION_CONTRACT.md).

| File | Purpose |
|---|---|
| `pick_place.json` | Identify, pick and place the orange screw at the delivery zone, then go home |
| `move_to_target.json` | Move to one named target |
| `invalid_action.json` | Deliberately rejected with `UNKNOWN_ACTION` |
| [`objects/`](objects) | One task per demo object, plus all four in turn |

```bash
python -m robot_control.cli validate --task examples/invalid_action.json
python -m robot_control.cli demo --backend mock --task examples/pick_place.json
```
