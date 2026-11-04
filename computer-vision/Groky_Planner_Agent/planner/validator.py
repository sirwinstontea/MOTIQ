"""Validates that an expanded primitive sequence adheres to physical robot rules and limits."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple
from .primitives import get_primitives_catalog


class PlanValidator:
    def __init__(self):
        cat = get_primitives_catalog()
        self.limits = cat["limits"]
        self.min_z = self.limits.get("min_z_above_table_m", 0.05)
        self.max_reach = self.limits.get("max_reach_m", 0.85)

    def validate(self, primitives: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Checks a sequence of expanded primitives. Returns (is_valid, list_of_errors)."""
        errors = []
        gripper_closed = False
        simulated_z = 0.20  # default safe starting TCP height

        for idx, step in enumerate(primitives):
            prim = step.get("primitive", "")
            args = step.get("args", {})

            if prim == "OPEN_GRIPPER":
                gripper_closed = False

            elif prim == "CLOSE_GRIPPER":
                gripper_closed = True

            elif prim == "MOVE_DOWN":
                dist = float(args.get("distance_m", 0.05))
                simulated_z -= dist
                if simulated_z < self.min_z:
                    errors.append(f"Step {idx} ({prim}): Target Z ({simulated_z:.3f}m) drops below table safety limit ({self.min_z}m).")

            elif prim == "MOVE_UP":
                dist = float(args.get("distance_m", 0.05))
                simulated_z += dist

            elif prim == "MOVE_ABOVE":
                # Simulated jump to target approach height
                # Object surface is assumed at safe height + offset
                height = float(args.get("height_m", 0.15))
                simulated_z = max(height, 0.05) + height
                if simulated_z < self.min_z:
                    errors.append(f"Step {idx} ({prim}): Approach height ({height:.3f}m) is below table safety limit ({self.min_z}m).")

            elif prim in ("MOVE_LEFT", "MOVE_RIGHT", "MOVE_FORWARD", "MOVE_BACKWARD"):
                # Horizontal translations at very low Z risk scraping the table
                if simulated_z <= self.min_z + 0.01:
                    errors.append(f"Step {idx} ({prim}): Horizontal move executed at dangerously low Z ({simulated_z:.3f}m). Lift before translating.")

        return len(errors) == 0, errors
