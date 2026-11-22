"""Installing RoboDK for the user. RoboDK's installer sits behind a sign-up form on robodk.com, so MOTIQ can't
fetch it by itself: it opens the download page, waits for the installer to land in Downloads, then runs it
silently (/S, the switch RoboDK documents for unattended installs) and waits until RoboDK.exe exists."""
from __future__ import annotations

import ctypes
import threading
import time
import webbrowser
from pathlib import Path

from . import detect, paths

DOWNLOAD_PAGE = "https://robodk.com/download"
WAIT_DOWNLOAD_S = 30 * 60
WAIT_INSTALL_S = 20 * 60


def _downloads() -> Path:
    return Path.home() / "Downloads"


def find_installer(newer_than: float = 0.0) -> Path | None:
    """Newest finished RoboDK installer in Downloads (a browser's partial .crdownload/.part file doesn't match)."""
    d = _downloads()
    if not d.is_dir():
        return None
    found = [p for p in d.glob("*.exe") if "robodk" in p.name.lower() and p.stat().st_mtime >= newer_than]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _run_elevated(exe: Path, args: str) -> bool:
    """RoboDK installs to C:\\RoboDK, which needs admin: ask Windows (UAC prompt) to run the installer."""
    if not paths.WIN:
        return False
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), args, str(exe.parent), 1)
    return rc > 32          # ShellExecute returns > 32 on success; the user declining UAC gives an error code


class RoboDKInstall:
    def __init__(self):
        self.state = "idle"      # idle | downloading | installing | done | failed
        self.message = ""
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        return {"state": self.state, "message": self.message}

    def start(self) -> dict:
        with self.lock:
            if detect.robodk():
                self.state, self.message = "done", "RoboDK is installed."
                return self.snapshot()
            if self.state in ("downloading", "installing"):
                return self.snapshot()
            self.state, self.message = "downloading", ""
        threading.Thread(target=self._run, daemon=True).start()
        return self.snapshot()

    def _run(self) -> None:
        try:
            exe = find_installer()
            if not exe:
                started = time.time() - 5
                self.message = "Download RoboDK in your browser. MOTIQ installs it as soon as it arrives."
                webbrowser.open(DOWNLOAD_PAGE)
                end = time.time() + WAIT_DOWNLOAD_S
                while not exe and time.time() < end:
                    time.sleep(2)
                    exe = find_installer(started)
                if not exe:
                    raise RuntimeError("No RoboDK download arrived in your Downloads folder.")
                time.sleep(3)    # let the browser finish renaming / scanning the file
            self.state, self.message = "installing", "Installing RoboDK. Click Yes if Windows asks."
            if not _run_elevated(exe, "/S"):
                raise RuntimeError("Windows didn't start the RoboDK installer (permission declined?).")
            end = time.time() + WAIT_INSTALL_S
            while time.time() < end:
                if detect.robodk():
                    self.state, self.message = "done", "RoboDK is installed."
                    return
                time.sleep(3)
            raise RuntimeError("The RoboDK installer didn't finish. Run it from Downloads, then try again.")
        except Exception as e:  # shown on the review screen
            self.state, self.message = "failed", str(e)
