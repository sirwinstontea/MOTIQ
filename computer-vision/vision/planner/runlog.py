"""Run log: JSON vivo dello stato della sessione agentica.

Riscritto in modo atomico a ogni transizione: UI/app possono leggerlo in polling
(`logs/runs/run_<ts>.json` e la copia `logs/runs/latest.json`).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from .library import atomic_write_json
from .primitives import ROOT
from .schemas import RunEvent, RunInput, RunState, RunStep, now_iso

DEFAULT_RUNS_DIR = ROOT / "logs" / "runs"


class RunLog:
    def __init__(self, state: RunState, path: Path, write_latest: bool = True):
        self.state = state
        self.path = path
        self.latest = path.parent / "latest.json" if write_latest else None
        self.save()

    @classmethod
    def create(cls, runs_dir: Path | str = DEFAULT_RUNS_DIR, input: Optional[RunInput] = None,
               executor: str = "", library_path: Optional[str] = None) -> "RunLog":
        runs_dir = Path(runs_dir)
        run_id = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        state = RunState(run_id=run_id, input=input or RunInput(), executor=executor,
                         library_path=library_path)
        return cls(state, runs_dir / f"run_{run_id}.json")

    def save(self) -> None:
        self.state.updated_at = now_iso()
        data = self.state.model_dump(mode="json")
        atomic_write_json(self.path, data)
        if self.latest is not None:
            atomic_write_json(self.latest, data)

    def update(self, **fields: Any) -> None:
        for k, v in fields.items():
            setattr(self.state, k, v)
        self.save()

    def event(self, kind: str, message: str = "", **data: Any) -> None:
        self.state.events.append(RunEvent(kind=kind, message=message, data=data))
        self.save()

    def set_steps(self, steps: list[RunStep]) -> None:
        self.state.steps = steps
        self.state.current_step = None
        self.save()

    def step(self, idx: int, **fields: Any) -> None:
        st = self.state.steps[idx]
        for k, v in fields.items():
            setattr(st, k, v)
        self.state.current_step = idx
        self.save()

    def fail(self, message: str, status: str = "failed") -> None:
        self.state.error = message
        self.state.status = status  # type: ignore[assignment]
        self.event("run_failed" if status == "failed" else "run_stopped", message)
