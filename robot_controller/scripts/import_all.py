#!/usr/bin/env python3
"""Thin wrapper: python scripts/import_all.py [args]  ==  python -m robot_control.sim_objects.sim_import [args]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_control.sim_objects.sim_import import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
