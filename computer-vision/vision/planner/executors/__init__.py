from .base import Executor, ExecutorError
from .mock_exec import MockExecutor


def make_executor(kind: str, **kw) -> Executor:
    if kind == "mock":
        return MockExecutor(**{k: v for k, v in kw.items() if k in MockExecutor.INIT_KEYS})
    if kind == "robodk":
        from .robodk_exec import RoboDKExecutor  # import pigro: robodk è opzionale
        return RoboDKExecutor(**{k: v for k, v in kw.items() if k in RoboDKExecutor.INIT_KEYS})
    raise ValueError(f"executor sconosciuto: {kind!r} (mock|robodk)")


__all__ = ["Executor", "ExecutorError", "MockExecutor", "make_executor"]
