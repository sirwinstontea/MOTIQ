"""Live status output for demos: terminal view, live JSON file, telemetry thread.

* Terminal: one block per step ("NOW ... / next ...") plus, on a real
  terminal, a self-updating line with TCP position, joints and gripper.
* File: ``runs/live_status.json``, rewritten atomically on every step and
  telemetry tick, so other processes (app / Grok / dashboard) can poll it.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

_ICON = {"done": "✓", "skipped": "·", "running": "▶", "failed": "✗", "pending": " "}


def _fmt_xyz(v) -> str:
    return "(" + ", ".join(f"{x:+.3f}" for x in v) + ") m" if v else "-"


class LiveView:
    def __init__(self, stream=None, live_file: Optional[str] = None):
        self.out = stream or sys.stdout
        self.tty = self.out.isatty()
        self.live_file = Path(live_file) if live_file else None
        self._lock = threading.Lock()
        self._status_line = ""
        self._last_step = None

    # ---- output helpers ------------------------------------------------------
    def log(self, line: str) -> None:
        with self._lock:
            if self.tty:
                self.out.write("\r\033[K")
            self.out.write(line + "\n")
            if self.tty and self._status_line:
                self.out.write(self._status_line)
            self.out.flush()

    def _set_status_line(self, text: str) -> None:
        with self._lock:
            self._status_line = text
            if self.tty:
                self.out.write("\r\033[K" + text)
                self.out.flush()

    def write_file(self, snap: dict) -> None:
        if not self.live_file:
            return
        try:
            self.live_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.live_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap, indent=2), encoding="utf-8")
            os.replace(tmp, self.live_file)
        except OSError:
            pass

    # ---- callbacks -----------------------------------------------------------
    def on_step(self, snap: dict) -> None:
        self.write_file(snap)
        now, nxt = snap.get("now"), snap.get("next")
        prog = snap.get("progress", {})
        key = (now or {}).get("index"), snap.get("task_status")
        if key == self._last_step:
            return
        self._last_step = key
        if now:
            tgt = f"  {now['motion']} → {now['target_id']} {_fmt_xyz(now.get('target_xyz_m'))}" if now.get("target_id") else ""
            self.log(f"▶ [{now['index']:>2}/{prog.get('total')}] {snap['state']:<14} NOW : {now['description']}{tgt}")
            if nxt:
                ntgt = f" ({nxt['target_id']})" if nxt.get("target_id") else ""
                self.log(f"   {'':<20}  NEXT: {nxt['description']}{ntgt}")
            else:
                self.log(f"   {'':<20}  NEXT: (last step)")
        if not self.tty:
            r = snap.get("robot") or {}
            if r.get("tcp_m"):
                self.log(f"   {'':<20}  AT  : tcp {_fmt_xyz(r['tcp_m'])}  gripper={r.get('gripper')}"
                         f"  holding={r.get('holding') or '-'}")

    def on_telemetry(self, snap: dict) -> None:
        self.write_file(snap)
        r = snap.get("robot") or {}
        if not r.get("tcp_m"):
            return
        j = " ".join(f"{q:+6.1f}" for q in (r.get("joints_deg") or []))
        p = snap.get("progress", {})
        self._set_status_line(f"   ⟳ {p.get('done')}/{p.get('total')}  tcp {_fmt_xyz(r['tcp_m'])}  "
                              f"joints[°] {j}  gripper={r.get('gripper')}  holding={r.get('holding') or '-'}"
                              f"{'  moving' if r.get('moving') else ''}")

    def finish(self) -> None:
        with self._lock:
            if self.tty and self._status_line:
                self.out.write("\n")
                self.out.flush()
            self._status_line = ""


class Telemetry:
    """Polls ``snapshot()`` every ``period`` seconds and hands it to ``sink``."""

    def __init__(self, snapshot: Callable[[], dict], sink: Callable[[dict], None], period: float = 0.25):
        self.snapshot, self.sink, self.period = snapshot, sink, period
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True, name="telemetry")

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(2)

    def _run(self):
        while not self._stop.wait(self.period):
            try:
                self.sink(self.snapshot())
            except Exception:
                pass
