# installer

Builds `dist/MOTIQ-Setup.exe`, a one-click Windows installer that needs no admin rights.

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

Build needs git, [uv](https://docs.astral.sh/uv/) and [Inno Setup](https://jrsoftware.org/isdl.php) 6.3+ (`ISCC.exe` on
PATH, in its default folder, or set `$env:ISCC`). Upload the result to a GitHub Release.

`build.ps1` does four things:

1. Stages a clean copy of the repo in `stage/`: tracked and new files, nothing gitignored, so no `.env` keys, venvs,
   `node_modules` or `.motiq`.
2. Freezes the [launcher](../launcher) into `MOTIQ.exe` with PyInstaller (no console window).
3. Adds `tools/uv.exe`, so the wizard can install Python 3.12 without anything preinstalled.
4. Compiles `motiq.iss`.

What the installer does:

- **Install:** copies everything to `%LOCALAPPDATA%\Programs\MOTIQ`, adds Start menu entries (MOTIQ, MOTIQ Settings,
  Stop MOTIQ) and a desktop shortcut, then opens MOTIQ.exe straight away. The MOTIQ setup wizard takes over from
  there. The installer has no pages of its own.
- **Upgrade:** stops a running MOTIQ first. It keeps `.motiq/` (answers and keys), the venvs, `node_modules` and the
  user's `robot-chooser/hardware.yaml` and `catalog.yaml`.
- **Uninstall:** stops MOTIQ and removes the whole folder, including venvs, downloads and saved keys.

`motiq.ico` is the Groky orb.
