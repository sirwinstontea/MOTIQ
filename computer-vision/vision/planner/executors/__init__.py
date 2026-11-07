from .base import Executor, ExecutorError
from .mock_exec import MockExecutor


def make_executor(kind: str, **kw) -> Executor:
    if kind == "mock":
        return MockExecutor(**{k: v for k, v in kw.items() if k in MockExecutor.INIT_KEYS})
    if kind == "pybullet":
        return MockExecutor(**{k: v for k, v in kw.items() if k in MockExecutor.INIT_KEYS})
    raise ValueError(f"executor sconosciuto: {kind!r} (mock|pybullet)")


__all__ = ["Executor", "ExecutorError", "MockExecutor", "make_executor"]
