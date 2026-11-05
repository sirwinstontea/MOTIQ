"""Components -> what the arm carries (mass, TCP, gripper) and how each device is connected."""
from __future__ import annotations

from .components import Component, TypeRegistry, on_arm


def analyse_components(reg: TypeRegistry, components: list[Component]) -> dict:
    issues: list[dict] = []

    def add(level, code, msg):
        issues.append({"level": level, "code": code, "message": msg})

    spec = {c.id: reg.validate_spec(c) for c in components}      # validated on load; defaults filled in
    carried = on_arm(components)
    carried_ids = {c.id for c in carried}
    effectors = [c for c in carried if "end_effector" in reg.get(c.type).roles]
    if len(effectors) > 1:
        add("blocker", "TOOLING", "more than one end effector carried by the arm: "
            + ", ".join(c.id for c in effectors) + " - the controller has one TCP")
    eff = effectors[0] if effectors else None

    mass, unknown = 0.0, []
    for c in carried:
        f = reg.get(c.type).payload_field
        if f is None:
            continue
        if spec[c.id].get(f) is None:
            unknown.append(c.id)
        else:
            mass += float(spec[c.id][f])
    if unknown:
        add("blocker", "PAYLOAD", "unknown mass of hardware carried by the arm: " + ", ".join(unknown))

    if eff is None:
        tcp, kind = [0.0] * 6, "none"
        add("info", "TOOLING", "no end effector on the arm: TCP = flange, no grasping")
    else:
        es = spec[eff.id]
        tcp, kind = list(es.get("tcp_offset_m_rad") or [0.0] * 6), es.get("sim_gripper") or "none"
        if kind == "symbolic":
            add("info", "GRIPPER", "symbolic gripper: grasps are simulated, never a physical grip confirmation")
        if es.get("actuated") is False:
            add("warning", "GRIPPER", f"the real tool ({eff.name}) does not open/close: grasp/release only work "
                "in simulation")

    connections = []
    for c in components:
        t = reg.get(c.type)
        d = t.driver(c.driver)
        if d is None:
            conn = "documented only (no driver)"
        elif not d.physical:
            conn = f"simulated via {d.connects_via}"
        elif not d.actuates:
            conn = f"physical, read-only via {d.connects_via}"
        else:
            conn = f"physical actuator via {d.connects_via} - never enabled by robot-chooser"
            add("warning", "CONNECTION", f"{c.name}: driver '{d.id}' moves real hardware through {d.connects_via}; "
                "robot-chooser only documents it (simulation-only build)")
        connections.append({"id": c.id, "type": c.type, "name": c.name, "attached_to": c.attached_to,
                            "carried_by_arm": c.id in carried_ids, "driver": c.driver, "connection": conn,
                            "status": c.status})
        if c.status == "placeholder":
            add("warning", "PLACEHOLDER", f"{c.name}: values are placeholders - measure them or take them "
                "from the datasheet, then set status")
        if "camera" in t.roles and not spec[c.id].get("calibrated"):
            add("warning", "CAMERA", f"camera {c.name} is not calibrated: its detections do not give executable "
                "pick coordinates")
    return {"issues": issues, "end_effector": eff.id if eff else None, "gripper_kind": kind,
            "tcp_offset_m_rad": tcp, "carried_mass_kg": round(mass, 4), "unknown_mass": unknown,
            "connections": connections}


def migrate_v1(data: dict) -> dict:
    """hardware.yaml v1 had fixed `tool` / `cameras` keys; turn them into components."""
    if "tool" not in data and "cameras" not in data:
        return data
    data = dict(data)
    comps = list(data.get("components") or [])
    t = data.pop("tool", None)
    if isinstance(t, dict):
        measured = t.get("mass_status") == "measured" and t.get("tcp_status") == "measured"
        sim = t.get("gripper_kind", "symbolic")
        comps.append({"id": "main_tool", "type": "gripper", "name": t.get("name", "tool"), "attached_to": "arm",
                      "driver": "controller_symbolic" if sim == "symbolic" else None,
                      "status": "measured" if measured else "placeholder", "notes": t.get("notes"),
                      "spec": {"kind": "parallel" if t.get("actuated") else "rigid",
                               "actuated": bool(t.get("actuated", False)), "mass_kg": t.get("mass_kg"),
                               "tcp_offset_m_rad": t.get("tcp_offset_m_rad"), "sim_gripper": sim}})
    for cam in data.pop("cameras", None) or []:
        s = {"role": cam.get("role"), "serial": cam.get("serial"), "calibrated": cam.get("calibrated", False)}
        comps.append({"id": cam["id"], "type": "camera", "name": cam.get("name", cam["id"]), "attached_to": "cell",
                      "driver": None, "status": "placeholder", "notes": cam.get("mount"),
                      "spec": {k: v for k, v in s.items() if v is not None}})
    data["components"] = comps
    data["schema"] = "motiq_hardware/2"
    return data
