"""Interfaccia comune degli executor (PyBullet, Mock)."""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from ..schemas import ConcretePrimitive, PositionMM, SceneObject, StepFeedback


class ExecutorError(RuntimeError):
    pass


@runtime_checkable
class Executor(Protocol):
    name: str

    def connect(self) -> None: ...

    def execute(self, prim: ConcretePrimitive) -> StepFeedback: ...

    def tcp_pose(self) -> Optional[list[float]]:
        """Posizione TCP [x, y, z] in mm nel frame base, se nota."""
        ...

    def home_tcp(self) -> Optional[list[float]]:
        """Posizione TCP [x, y, z] in mm alla configurazione home, se nota."""
        ...

    def object_pose(self, name: str) -> Optional[PositionMM]: ...

    def scene(self) -> list[SceneObject]: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...
