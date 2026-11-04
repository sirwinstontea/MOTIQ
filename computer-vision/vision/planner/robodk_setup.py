"""Prepara la stazione RoboDK per il planner (da lanciare con RoboDK aperto).

    python -m vision.planner.robodk_setup [--scene tests/fixtures/planner/tokens_unscrew.json]

- Richiede un robot già caricato nella stazione (es. UR5e da File > Open online library).
- Se il robot non ha un tool, aggiunge "Gripper" (TCP a 150 mm dalla flangia, senza geometria).
- Crea come scatole gli oggetti della scena (scene_objects con position_mm, nel frame base
  del robot) se non esistono già, con l'origine sul fondo dell'oggetto.
- Porta il robot in home.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .primitives import ROOT, default_catalog

DEFAULT_SCENE = ROOT / "tests" / "fixtures" / "planner" / "tokens_unscrew.json"
SIZES_MM = {"cap": (40, 40, 30), "bucket": (160, 160, 120)}
COLORS = {"bianco": [0.95, 0.95, 0.95, 1], "white": [0.95, 0.95, 0.95, 1], "blu": [0.1, 0.3, 0.9, 1],
          "blue": [0.1, 0.3, 0.9, 1], "rosso": [0.9, 0.1, 0.1, 1], "red": [0.9, 0.1, 0.1, 1],
          "verde": [0.1, 0.7, 0.2, 1], "green": [0.1, 0.7, 0.2, 1]}


def box_triangles(sx: float, sy: float, sz: float) -> list[list[float]]:
    """Triangoli di una scatola centrata in xy con il fondo a z=0 (mm)."""
    x, y = sx / 2, sy / 2
    v = [[-x, -y, 0], [x, -y, 0], [x, y, 0], [-x, y, 0],
         [-x, -y, sz], [x, -y, sz], [x, y, sz], [-x, y, sz]]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [v[i] for f in faces for i in f]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", default=str(DEFAULT_SCENE), help="VideoTokens JSON o lista di SceneObject")
    ap.add_argument("--robot", help="nome del robot (default: il primo)")
    ap.add_argument("--tcp-mm", type=float, default=150.0)
    a = ap.parse_args(argv)

    from robodk import robolink as R
    from robodk import robomath as M

    rdk = R.Robolink()
    robot = rdk.Item(a.robot or "", R.ITEM_TYPE_ROBOT)
    if not robot.Valid():
        print("Nessun robot nella stazione: in RoboDK usa File > Open online library, cerca 'UR5e', "
              "Download, poi rilancia questo script.")
        return 1
    print(f"robot: {robot.Name()}")

    tool = robot.getLink(R.ITEM_TYPE_TOOL)
    if not tool.Valid():
        tool = robot.AddTool(M.transl(0, 0, a.tcp_mm), "Gripper")
        print(f"aggiunto tool 'Gripper' (TCP z={a.tcp_mm} mm)")
    else:
        print(f"tool: {tool.Name()}")

    raw = json.loads(Path(a.scene).read_text(encoding="utf-8"))
    objects = raw["scene_objects"] if isinstance(raw, dict) else raw
    base = robot.Parent()
    for o in objects:
        pos = o.get("position_mm")
        if not pos:
            continue
        name = o["name"]
        if rdk.Item(name, R.ITEM_TYPE_OBJECT).Valid():
            print(f"oggetto '{name}' già presente")
            continue
        size = next((s for k, s in SIZES_MM.items() if k in (o.get("label") or name)), (50, 50, 50))
        shape = rdk.AddShape(M.Mat(box_triangles(*size)).tr())  # RoboDK vuole 3xN; parent dopo
        if not shape.Valid():
            print(f"impossibile creare '{name}'")
            return 1
        shape.setParent(base)
        shape.setName(name)
        shape.setPose(M.transl(pos["x"], pos["y"], pos.get("z", 0.0)))
        col = COLORS.get((o.get("color") or "").lower())
        if col:
            shape.setColor(col)
        print(f"creato '{name}' {size} mm in ({pos['x']}, {pos['y']}, {pos.get('z', 0)}) nel frame base")

    robot.setJoints(list(default_catalog().robot.home_joints_deg))
    print("robot in home. Stazione pronta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
