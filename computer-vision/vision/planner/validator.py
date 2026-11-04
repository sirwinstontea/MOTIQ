"""Validatore deterministico della sequenza di primitive espansa.

Simula la posa del TCP (metri, frame base) e lo stato della pinza e controlla le regole
fisiche del braccio. Gli errori sono testi leggibili che vengono rimandati a Grok.
Non sostituisce i limiti di sicurezza del controller: è un filtro a monte.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .primitives import LINEAR_PRIMITIVES, Catalog, default_catalog
from .schemas import ConcretePrimitive

HORIZONTAL = {"MOVE_LEFT", "MOVE_RIGHT", "MOVE_FORWARD", "MOVE_BACKWARD"}
SMALL_HORIZONTAL_M = 0.03  # spostamenti minimi tollerati a quota bassa (es. allineamento)


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _State:
    pos: Optional[list[float]]  # [x, y, z] in metri, None = sconosciuta
    gripper: str = "unknown"  # open|closed|unknown
    holding: bool = False


def validate_sequence(
    prims: Sequence[ConcretePrimitive],
    catalog: Optional[Catalog] = None,
    initial_tcp_m: Optional[Sequence[float]] = None,
    home_tcp_m: Optional[Sequence[float]] = None,
    table_z_m: float = 0.0,
    initial_gripper: str = "unknown",
) -> ValidationReport:
    cat = catalog or default_catalog()
    lim = cat.robot.limits
    rep = ValidationReport()
    st = _State(pos=list(initial_tcp_m) if initial_tcp_m is not None else None, gripper=initial_gripper)

    z_min = table_z_m + lim.get("min_z_above_table_m", 0.01)
    z_low = table_z_m + 0.5 * lim.get("safe_travel_height_m", 0.15)
    v_max = lim.get("max_tcp_speed_mps", 0.25)
    w_max = lim.get("max_joint_speed_dps", 60)
    reach = cat.robot.reach_m

    for i, p in enumerate(prims):
        where = f"step {i + 1} {p.primitive} [{' > '.join(p.skill_path)}]"
        spec = cat.get(p.primitive)
        if spec is None:
            rep.errors.append(f"{where}: primitiva fuori catalogo")
            continue
        a = p.args

        if "speed_mps" in a and a["speed_mps"] > v_max:
            rep.errors.append(f"{where}: speed_mps={a['speed_mps']} > limite {v_max}")
        if "speed_dps" in a and a["speed_dps"] > w_max:
            rep.errors.append(f"{where}: speed_dps={a['speed_dps']} > limite {w_max}")

        low = st.pos is not None and st.pos[2] < z_low
        new_pos: Optional[list[float]] = st.pos

        if p.primitive in LINEAR_PRIMITIVES:
            d = float(a.get("distance_m", 0.0))
            vec = cat.robot.axis_vector(spec.direction or "up")
            if p.primitive == "MOVE_DOWN" and not st.holding:
                if st.gripper == "closed":
                    rep.errors.append(f"{where}: discesa con pinza chiusa e vuota: "
                                      f"serve OPEN_GRIPPER prima di scendere")
                elif st.gripper == "unknown":
                    rep.warnings.append(f"{where}: stato pinza sconosciuto prima della discesa")
            if p.primitive in HORIZONTAL and low and d > SMALL_HORIZONTAL_M:
                rep.errors.append(f"{where}: traslazione orizzontale di {d:.3f} m a quota bassa "
                                  f"(z={st.pos[2]:.3f} m): risalire prima (MOVE_UP)")
            if st.pos is not None:
                new_pos = [st.pos[k] + vec[k] * d for k in range(3)]

        elif p.primitive in ("MOVE_ABOVE", "MOVE_TO_POSE"):
            if p.primitive == "MOVE_ABOVE":
                tgt = a.get("target_mm")
                target = ([tgt[0] / 1000, tgt[1] / 1000, tgt[2] / 1000 + float(a.get("height_m", 0.15))]
                          if tgt else None)
                if target is not None and float(a.get("height_m", 0.15)) < 0.03:
                    rep.warnings.append(f"{where}: height_m molto bassa ({a.get('height_m')})")
            else:
                target = [float(a["x"]), float(a["y"]), float(a["z"])]
            if target is not None and st.pos is not None and low:
                dxy = math.hypot(target[0] - st.pos[0], target[1] - st.pos[1])
                if dxy > SMALL_HORIZONTAL_M:
                    rep.errors.append(f"{where}: spostamento orizzontale di {dxy:.3f} m partendo da "
                                      f"quota bassa (z={st.pos[2]:.3f} m): risalire prima (MOVE_UP)")
            new_pos = target

        elif p.primitive == "HOME":
            if low and st.holding is False and st.gripper == "closed":
                rep.warnings.append(f"{where}: HOME partendo da quota bassa")
            elif low:
                rep.warnings.append(f"{where}: HOME partendo da quota bassa: meglio risalire prima")
            new_pos = list(home_tcp_m) if home_tcp_m is not None else None

        elif p.primitive in ("MOVE_JOINT", "MOVE_JOINTS"):
            new_pos = None  # posa cartesiana non nota senza cinematica diretta

        elif p.primitive == "OPEN_GRIPPER":
            st.gripper, st.holding = "open", False
        elif p.primitive == "CLOSE_GRIPPER":
            st.gripper, st.holding = "closed", True

        if new_pos is not None:
            if new_pos[2] < z_min - 1e-9:
                rep.errors.append(f"{where}: quota finale z={new_pos[2]:.3f} m sotto il minimo "
                                  f"{z_min:.3f} m (tavolo + min_z_above_table_m)")
            dist = math.sqrt(sum(c * c for c in new_pos))
            if dist > reach:
                rep.errors.append(f"{where}: posizione a {dist:.3f} m dalla base, oltre lo sbraccio "
                                  f"{reach} m")
        st.pos = new_pos
    return rep
