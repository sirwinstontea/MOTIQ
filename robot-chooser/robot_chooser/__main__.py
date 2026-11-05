"""CLI.

  python -m robot_chooser list [--brand Elite] [--dof 6] [--text ur]
  python -m robot_chooser status                       # requirements of the active setup
  python -m robot_chooser select ur_ur3e
  python -m robot_chooser set tool.mass_kg 0.35        # any hardware.yaml key (value parsed as YAML)
  python -m robot_chooser set-robot ur_ur3e scene_file ur_ur3e.yaml
  python -m robot_chooser apply                        # re-check + regenerate after hand edits
  python -m robot_chooser serve [--port 8770]          # local web UI
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
