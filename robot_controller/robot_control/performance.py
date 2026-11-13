"""Task-local latency measurements, not a motion/state cache or a safety decision."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter


class PerformanceRecorder:
    def __init__(self, clock=perf_counter):
        self.clock = clock
        self.started = clock()
        self.stages = {}
        self.counters = {}

    def snapshot(self):
        return {"elapsed_ms": round((self.clock() - self.started) * 1000, 3),
                "stages": {name: {**values, "total_ms": round(values["total_ms"], 3),
                                   "max_ms": round(values["max_ms"], 3)}
                           for name, values in self.stages.items()},
                "counters": dict(self.counters)}


_recorder = ContextVar("motiq_task_performance", default=None)


@contextmanager
def record_task():
    recorder = PerformanceRecorder()
    token = _recorder.set(recorder)
    try:
        yield recorder
    finally:
        _recorder.reset(token)


def increment(name, amount=1):
    recorder = _recorder.get()
    if recorder is not None:
        recorder.counters[name] = recorder.counters.get(name, 0) + amount


def snapshot():
    recorder = _recorder.get()
    return recorder.snapshot() if recorder is not None else {}


@contextmanager
def measure(name):
    recorder = _recorder.get()
    if recorder is None:
        yield
        return
    started = recorder.clock()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        elapsed = (recorder.clock() - started) * 1000
        stage = recorder.stages.setdefault(name, {"calls": 0, "failures": 0, "total_ms": 0.0, "max_ms": 0.0})
        stage["calls"] += 1
        stage["failures"] += int(failed)
        stage["total_ms"] += elapsed
        stage["max_ms"] = max(stage["max_ms"], elapsed)


def timed(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with measure(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
