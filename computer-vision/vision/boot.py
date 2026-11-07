"""Load torchvision once, on the calling thread, before any worker starts.

YOLO warmup does ``import torchvision`` while YOLO-World (and torch.jit) imports
``torchvision.transforms.functional`` on another thread. Those two imports
overlap inside ``torchvision.transforms`` and raise::

    ImportError: cannot import name 'InterpolationMode'
    from partially initialized module 'torchvision.transforms'

A finished import is only a dict lookup, so later threads are safe.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_done = False


def load_torchvision() -> None:
    global _done
    if _done:
        return
    with _lock:
        if _done:
            return
        import torchvision  # noqa: F401
        from torchvision.transforms.functional import InterpolationMode
        import torchvision.transforms as transforms

        # The package init sometimes returns before this name is bound. Pin it.
        if not hasattr(transforms, "InterpolationMode"):
            transforms.InterpolationMode = InterpolationMode
        _done = True
