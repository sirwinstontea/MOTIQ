"""Turning skill steps into tool waypoints and testing them against the arm kinematics.

This is not a trajectory planner. It answers one question per waypoint chain: *does a joint-space
solution exist that stays inside the joint limits, above the table, away from singularities, and
continuous along every straight-line segment the skill needs?* It does so with the closed-form
IK of the UR arm, a dynamic-programming search over the (<= 8) IK branches at each sample, and the
manufacturer's joint limits.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .actions import resolve_action
from .kinematics import URKinematics, make_T, rot_x, rot_z, tool_down_rotation, wrap_to_pi
from .profiles import Profiles
from .skill_model import SceneObject, Skill, Step, as_float, find_value, parse_vec3

UP = np.array([0.0, 0.0, 1.0])


# ----------------------------------------------------------------------------- rotations
def rot_log(R: np.ndarray) -> np.ndarray:
    c = (np.trace(R) - 1.0) / 2.0
    c = max(-1.0, min(1.0, c))
    th = math.acos(c)
    if th < 1e-9:
        return np.zeros(3)
    if abs(math.pi - th) < 1e-6:
        # near 180 deg: axis from the diagonal
        A = (R + np.eye(3)) / 2.0
        k = int(np.argmax(np.diag(A)))
        v = A[:, k] / math.sqrt(max(A[k, k], 1e-12))
        return v / np.linalg.norm(v) * th
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2.0 * math.sin(th))
    return w * th


def rot_exp(w: np.ndarray) -> np.ndarray:
    th = float(np.linalg.norm(w))
    if th < 1e-12:
        return np.eye(3)
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * (K @ K)


def slerp(R0: np.ndarray, R1: np.ndarray, t: float) -> np.ndarray:
    return R0 @ rot_exp(rot_log(R0.T @ R1) * t)


def horizontal_radial_rotation(azimuth: float) -> np.ndarray:
    """Tool z axis horizontal, pointing radially outward along `azimuth`."""
    z = np.array([math.cos(azimuth), math.sin(azimuth), 0.0])
    x = np.cross(UP, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


# ----------------------------------------------------------------------------- data classes
@dataclass
class Waypoint:
    label: str
    step_index: int
    pos: np.ndarray       # TCP position in the robot base frame
    R: np.ndarray         # tool orientation (z axis = tool approach direction)
    edge: str = "free"    # how the tool gets here from the previous waypoint: "free" (joint move) | "linear" (straight line, orientation interpolated)
    holding: str | None = None
    note: str = ""
    q_fixed: np.ndarray | None = None   # joint configuration the robot is physically in here (home / joint moves): IK branch is NOT free


@dataclass
class PlanIssue:
    step_index: int
    status: str            # "unknown" | "fail"
    check: str
    message: str
    needs: list[str] = field(default_factory=list)


@dataclass
class PlanParams:
    tcp_offset_m: float
    container_height_m: float | None = None
    object_size_m: float | None = None

    def key(self) -> tuple:
        return (round(self.tcp_offset_m, 4), self.container_height_m, self.object_size_m)


@dataclass
class Plan:
    waypoints: list[Waypoint]
    issues: list[PlanIssue]
    unknown_container_height: bool = False
    unknown_object_size: bool = False
    assumed_notes: list[str] = field(default_factory=list)
    release_points: list[int] = field(default_factory=list)   # waypoint indexes where the object is let go
    hold_points: list[int] = field(default_factory=list)      # waypoint indexes where the object is picked up
    carried: dict = field(default_factory=dict)               # object id -> SceneObject carried during the plan
    seq_log: list = field(default_factory=list)               # primitive-by-primitive log (template schema 1.1)


# ----------------------------------------------------------------------------- planner
class Planner:
    def __init__(self, skill: Skill, profiles: Profiles):
        self.skill = skill
        self.p = profiles
        self.cell = profiles.cell
        self.scale = skill.unit_scale

    # -- coordinates -------------------------------------------------------------------------
    def to_base(self, p_skill: np.ndarray) -> np.ndarray:
        p = np.asarray(p_skill, dtype=float)
        if self.skill.frame == "base":
            return p.copy()
        return self.cell.world_to_base(p)

    def vec_param(self, step: Step, names: tuple[str, ...]) -> np.ndarray | None:
        v = find_value(step.params, names, max_depth=2)
        p = parse_vec3(v)
        return None if p is None else p * self.scale

    def length_param(self, step: Step, names: tuple[str, ...]) -> float | None:
        v = find_value(step.params, names, max_depth=2)
        x = as_float(v)
        return None if x is None else x * self.scale

    # -- helpers -----------------------------------------------------------------------------
    def preserve_orientation(self, step: Step, held: SceneObject | None) -> bool:
        v = find_value(step.params, ("preserve_orientation", "keep_upright", "keep_orientation", "upright"), max_depth=2)
        if isinstance(v, bool):
            return v
        if held is not None:
            for c in self.skill.constraints.get("object_constraints", []) or []:
                if isinstance(c, dict) and str(c.get("type", "")).lower() in ("orientation", "upright") and str(c.get("object", "")) in (held.id, held.name):
                    return True
        return False

    def orientation_for(self, step: Step, prev_R: np.ndarray | None, at_xy: np.ndarray) -> tuple[np.ndarray, str]:
        grasp = str(find_value(step.params, ("grasp_type", "approach", "approach_direction"), max_depth=2) or "").lower()
        ori = find_value(step.params, ("orientation", "tool_orientation"), max_depth=2)
        if isinstance(ori, str):
            o = ori.lower()
            if o in ("horizontal", "forward", "front", "radial", "side"):
                return horizontal_radial_rotation(math.atan2(at_xy[1], at_xy[0])), "tool horizontal (radial)"
            if o in ("down", "tool_down", "vertical", "top", "downward"):
                return tool_down_rotation(), "tool pointing down"
        if isinstance(ori, dict):
            rpy = find_value(ori, ("rpy_deg",), max_depth=1)
            if isinstance(rpy, (list, tuple)) and len(rpy) == 3 and all(as_float(v) is not None for v in rpy):
                r, p_, y = (math.radians(float(v)) for v in rpy)
                Rz, Ry, Rx = rot_z(y), np.array([[math.cos(p_), 0, math.sin(p_)], [0, 1, 0], [-math.sin(p_), 0, math.cos(p_)]]), rot_x(r)
                return Rz @ Ry @ Rx, "explicit rpy"
        if any(k in grasp for k in ("side", "front", "lateral", "horizontal")):
            return horizontal_radial_rotation(math.atan2(at_xy[1], at_xy[0])), "tool horizontal (side grasp)"
        if prev_R is not None and not grasp:
            return prev_R.copy(), "orientation kept"
        return tool_down_rotation(), "tool pointing down"

    def container_geometry(self, obj: SceneObject, params: PlanParams, plan: Plan) -> tuple[np.ndarray, float, float]:
        """(centre xy+0 in base frame, bottom z, top z) of a container in the base frame."""
        assert obj.position is not None
        c = self.to_base(obj.position)
        size_z = None if obj.size is None else float(obj.size[2])
        z_pos = float(c[2])
        # does the stated z mark the centre or the base of the container?
        if size_z is not None and size_z > 0 and abs((obj.position[2]) - size_z / 2) <= 0.2 * size_z:
            bottom = z_pos - size_z / 2
        else:
            bottom = z_pos
        if obj.wall_height_m is not None:
            wall = obj.wall_height_m
        elif size_z is not None:
            wall = size_z
        elif params.container_height_m is not None:
            wall = params.container_height_m
            plan.unknown_container_height = True
        else:
            wall = 0.10
            plan.unknown_container_height = True
        return np.array([c[0], c[1], 0.0]), bottom, bottom + wall

    def held_height(self, held: SceneObject | None, params: PlanParams, plan: Plan) -> float:
        if held is not None and held.size is not None:
            return float(held.size[2])
        plan.unknown_object_size = True
        return params.object_size_m if params.object_size_m is not None else 0.05

    # -- main --------------------------------------------------------------------------------
    def build(self, params: PlanParams) -> Plan:
        plan = Plan(waypoints=[], issues=[])
        cell = self.cell
        held: SceneObject | None = None
        prev_pos: np.ndarray | None = None
        prev_R: np.ndarray | None = None
        approach = cell.approach_clearance_m
        retreat = cell.retreat_clearance_m

        def add(label: str, step: Step, pos: np.ndarray, R: np.ndarray, edge: str = "free", note: str = "") -> int:
            nonlocal prev_pos, prev_R
            plan.waypoints.append(Waypoint(label, step.index, np.asarray(pos, dtype=float), R, edge, held.id if held else None, note))
            prev_pos, prev_R = np.asarray(pos, dtype=float), R
            return len(plan.waypoints) - 1

        pstate: dict = {}

        def set_held(o):
            nonlocal held
            held = o

        def issue(st, status, check, msg, needs=None):
            plan.issues.append(PlanIssue(st.index, status, check, msg, needs or []))

        for step in self.skill.steps:
            if isinstance(step.raw.get("primitive_sequence"), list) and step.raw.get("primitive_sequence"):
                from .primitives import expand_primitives
                if prev_pos is not None:
                    pstate.update(pos=prev_pos, R=prev_R)
                expand_primitives(self, step, params, plan, add, pstate, lambda: held, set_held, issue)
                continue
            spec = resolve_action(step.action)
            if spec is None:
                plan.issues.append(PlanIssue(step.index, "unknown", "action", f"action '{step.action}' is not a known robot action; its hardware needs cannot be evaluated",
                                             [f"definition of what '{step.action}' physically does (target pose, tool use)"]))
                continue
            c = spec.canonical
            target = self.skill.object_by_ref(step.target)

            # ---------------------------------------------------------------- PICK
            if c == "PICK":
                pos = None
                if target is not None and target.position is not None:
                    pos = self.to_base(target.position)
                else:
                    ep = self.vec_param(step, ("position", "location", "pose", "target_position"))
                    if ep is not None:
                        pos = self.to_base(ep)
                if pos is None:
                    plan.issues.append(PlanIssue(step.index, "unknown", "target_position",
                                                 f"PICK target '{step.target}' has no known position",
                                                 [f"position of '{step.target}' (x, y, z) in the skill frame"]))
                    continue
                R, why = self.orientation_for(step, prev_R, pos[:2])
                zdir = R[:, 2]
                if abs(zdir[2]) > 0.7:     # approach from above: hover straight above
                    pre = pos + np.array([0, 0, approach])
                    lift = pos + np.array([0, 0, retreat])
                else:                      # horizontal approach: back off against the tool axis, lift upward afterwards
                    pre = pos - zdir * approach
                    lift = pos + np.array([0, 0, retreat])
                add("pre-grasp", step, pre, R, "free", why)
                plan.hold_points.append(add("grasp", step, pos, R, "linear"))
                held = target
                if held is not None:
                    plan.carried[held.id] = held
                add("lift", step, lift, R, "linear")
                continue

            # ---------------------------------------------------------------- PLACE
            if c == "PLACE":
                preserve = self.preserve_orientation(step, held)
                above_extra = cell.transit_clearance_m
                hh = self.held_height(held, params, plan)
                dest: np.ndarray | None = None
                place_z = None
                top_z = None
                if target is not None and target.position is not None:
                    if target.is_container:
                        centre, bottom, top = self.container_geometry(target, params, plan)
                        dest = centre
                        place_z = bottom + hh / 2 + 0.005
                        top_z = top
                    else:
                        cpos = self.to_base(target.position)
                        sz = float(target.size[2]) if target.size is not None else 2 * float(target.position[2])
                        dest = np.array([cpos[0], cpos[1], 0.0])
                        top_z = cpos[2] + sz / 2
                        place_z = top_z + hh / 2 + 0.002
                else:
                    ep = self.vec_param(step, ("position", "location", "destination", "pose", "target_position"))
                    if ep is not None:
                        b = self.to_base(ep)
                        dest = np.array([b[0], b[1], 0.0])
                        place_z = float(b[2])
                        top_z = place_z
                if dest is None or place_z is None:
                    plan.issues.append(PlanIssue(step.index, "unknown", "target_position",
                                                 f"PLACE target '{step.target}' has no known position",
                                                 [f"position of '{step.target}' (x, y, z) in the skill frame"]))
                    continue
                R = prev_R.copy() if (prev_R is not None and held is not None) else tool_down_rotation()
                above_z = max((top_z or place_z) + hh / 2 + above_extra, place_z + approach)
                edge_in = "linear" if (held is not None and preserve) else "free"
                add("above-place", step, np.array([dest[0], dest[1], above_z]), R, edge_in)
                plan.release_points.append(add("place", step, np.array([dest[0], dest[1], place_z]), R, "linear"))
                add("retreat", step, np.array([dest[0], dest[1], above_z]), R, "linear")
                if held is not None:
                    held = None
                continue

            # ---------------------------------------------------------------- MOVE / LIFT / LOWER / RETREAT
            if c in ("MOVE", "LIFT", "LOWER", "RETREAT"):
                if c == "MOVE":
                    wps = find_value(step.params, ("waypoints", "path", "via"), max_depth=1)
                    pts: list[np.ndarray] = []
                    if isinstance(wps, list):
                        for w in wps:
                            p = parse_vec3(w if not isinstance(w, dict) else w.get("position", w))
                            if p is not None:
                                pts.append(self.to_base(p * self.scale))
                    if not pts:
                        ep = self.vec_param(step, ("position", "location", "destination", "pose", "target_position", "goal"))
                        if ep is not None:
                            pts = [self.to_base(ep)]
                        elif target is not None and target.position is not None:
                            tp = self.to_base(target.position)
                            at_obj = find_value(step.params, ("at_object", "touch"), max_depth=1) is True
                            pts = [tp if at_obj else tp + np.array([0, 0, approach])]
                    if not pts:
                        plan.issues.append(PlanIssue(step.index, "unknown", "target_position",
                                                     f"MOVE has no resolvable destination (target '{step.target}')",
                                                     ["destination position (x, y, z) or a target object with a position"]))
                        continue
                    linear = find_value(step.params, ("linear", "cartesian", "straight"), max_depth=1) is True or (held is not None and self.preserve_orientation(step, held))
                    for i, p in enumerate(pts):
                        R, why = self.orientation_for(step, prev_R, p[:2])
                        add(f"move-{i + 1}", step, p, R, "linear" if linear and prev_pos is not None else "free", why)
                else:
                    if prev_pos is None or prev_R is None:
                        plan.issues.append(PlanIssue(step.index, "unknown", "start_pose", f"{c} needs a known current tool position but none has been established before this step",
                                                     ["a preceding MOVE/PICK/PLACE or an explicit start position"]))
                        continue
                    dz = self.length_param(step, ("height", "distance", "dz", "amount"))
                    dz = abs(dz) if dz is not None else (retreat if c != "LOWER" else approach)
                    sign = -1.0 if c == "LOWER" else 1.0
                    add(c.lower(), step, prev_pos + np.array([0, 0, sign * dz]), prev_R.copy(), "linear")
                continue

            # ---------------------------------------------------------------- PUSH
            if c == "PUSH":
                direction = self.vec_param(step, ("direction",))
                dest = self.vec_param(step, ("destination", "to", "goal", "target_position"))
                dist = self.length_param(step, ("distance", "stroke", "length"))
                if target is None or target.position is None:
                    plan.issues.append(PlanIssue(step.index, "unknown", "target_position", f"PUSH target '{step.target}' has no known position",
                                                 [f"position of '{step.target}'"]))
                    continue
                start_obj = self.to_base(target.position)
                if dest is not None:
                    vec = self.to_base(dest) - start_obj
                elif direction is not None and dist is not None:
                    n = np.linalg.norm(direction)
                    vec = (direction / n) * dist if n > 0 else None
                else:
                    vec = None
                if vec is None or np.linalg.norm(vec) < 1e-6:
                    plan.issues.append(PlanIssue(step.index, "unknown", "push_vector", "PUSH has no usable direction/distance or destination",
                                                 ["push direction and distance, or a destination position"]))
                    continue
                u = vec / np.linalg.norm(vec)
                half = float(target.size[0]) / 2 if target.size is not None else 0.03
                begin = start_obj - u * (half + 0.03)
                begin[2] = start_obj[2]
                R = tool_down_rotation()
                add("push-approach", step, begin + np.array([0, 0, approach]), R, "free")
                add("push-start", step, begin, R, "linear")
                add("push-end", step, begin + vec, R, "linear")
                add("push-retreat", step, begin + vec + np.array([0, 0, retreat]), R, "linear")
                continue

            # ---------------------------------------------------------------- TILT (pour)
            if c == "TILT":
                if prev_pos is None or prev_R is None:
                    plan.issues.append(PlanIssue(step.index, "unknown", "start_pose", "TILT needs a known current tool pose", ["a preceding step that positions the tool"]))
                    continue
                ang = as_float(find_value(step.params, ("angle_deg", "angle"), max_depth=1))
                ang = 90.0 if ang is None else ang
                add("tilt", step, prev_pos.copy(), prev_R @ rot_x(math.radians(ang)), "linear", f"tilt {ang:g} deg about the tool x axis")
                continue

            # ROTATE / RELEASE / GRIP / HOME / WAIT / DETECT / VERIFY need no waypoint here
        return plan


# ----------------------------------------------------------------------------- evaluator
@dataclass
class Node:
    q: np.ndarray
    cost: float
    parent: "Node | None"
    samples: list[np.ndarray]   # joint configs along the edge from parent (ending at q); [] for roots
    wp: int
    inv_cond_min: float = 1.0


@dataclass
class WaypointResult:
    index: int
    reached: bool
    reason: str = ""
    detail: str = ""
    fail_fraction: float | None = None
    nodes: int = 0


@dataclass
class PlanResult:
    feasible: bool
    waypoints: list[WaypointResult]
    paths: list[list[np.ndarray]]        # joint-space sample chains (one per connected island)
    min_time_s: float
    cartesian_length_m: float
    inv_cond_min: float
    stretch_max: float
    clearance_min: float


class Evaluator:
    def __init__(self, profiles: Profiles):
        self.p = profiles
        self.kin: URKinematics = profiles.robot.kinematics()
        self.cell = profiles.cell
        r = profiles.robot
        self.vmax = r.joint_speed_max_rad_s
        self.max_reach_chain = abs(r.a[1]) + abs(r.a[2])
        self.table_z = profiles.cell.table_z_base_m
        self.margin_stretch = 1.0 - profiles.cell.reach_margin_m / self.max_reach_chain
        self.sing_hard = profiles.cell.sing_warn / 3.0
        self.joint_step = np.array([profiles.cell.max_joint_step_rad] * 3 + [profiles.cell.max_wrist_joint_step_rad] * 3)
        self._ik_cache: dict = {}

    # -- node tests --------------------------------------------------------------------------
    def clearance(self, q: np.ndarray) -> float:
        """Smallest distance between any arm segment surface and the table plane (m)."""
        o = self.kin.joint_origins(q)
        rad = self.p.robot.segment_radius_m
        best = 1e9
        for i in range(1, 6):          # upper arm .. wrist 3 (the base column sits on the table by design)
            a, b = o[i], o[i + 1]
            r = rad[i] if i < len(rad) else rad[-1]
            for t in (0.0, 0.5, 1.0):
                z = a[2] * (1 - t) + b[2] * t
                best = min(best, z - r - self.table_z)
        return float(best)

    def stretch(self, q: np.ndarray) -> float:
        o = self.kin.joint_origins(q)
        return float(np.linalg.norm(o[3] - o[1]) / self.max_reach_chain)

    def tool_T(self, pos: np.ndarray, R: np.ndarray, L: float) -> np.ndarray:
        return make_T(R, pos - L * R[:, 2])

    def ik_nodes(self, T: np.ndarray, linear: bool) -> tuple[list[tuple[np.ndarray, float, float, float]], Counter]:
        """Valid IK solutions at a pose: list of (q, inv_cond, stretch, clearance) + rejection reasons."""
        key = (np.round(T, 6).tobytes(), linear)
        hit = self._ik_cache.get(key)
        if hit is not None:
            return hit[0], Counter(hit[1])
        res = self._ik_nodes_uncached(T, linear)
        self._ik_cache[key] = (res[0], dict(res[1]))
        return res

    def _ik_nodes_uncached(self, T: np.ndarray, linear: bool) -> tuple[list[tuple[np.ndarray, float, float, float]], Counter]:
        why: Counter = Counter()
        sols = self.kin.ik(T)
        if not sols:
            why["no_ik_solution"] += 1
            return [], why
        out = []
        for q in sols:
            if not self.kin.within_limits(q):
                why["joint_limit"] += 1
                continue
            st = self.stretch(q)
            if st > self.margin_stretch:
                why["reach_margin"] += 1
                continue
            cl = self.clearance(q)
            if cl < self.p.robot.table_clearance_margin_m:
                why["table_collision"] += 1
                continue
            ic = self.kin.manipulability_info(q)["inv_condition"]
            if linear and ic < self.sing_hard:
                why["singularity"] += 1
                continue
            out.append((q, ic, st, cl))
        return out, why

    @staticmethod
    def jdiff(q0: np.ndarray, q1: np.ndarray) -> np.ndarray:
        """Shortest signed joint-angle difference q1 - q0 (a joint crossing +-180 deg is not a jump)."""
        return np.array([wrap_to_pi(float(b - a)) for a, b in zip(q0, q1)])

    def joint_edge_ok(self, q0: np.ndarray, q1: np.ndarray) -> bool:
        d = np.abs(self.jdiff(q0, q1))
        return bool(np.all(d <= self.joint_step))

    def move_time(self, q0: np.ndarray, q1: np.ndarray) -> float:
        return float(np.max(np.abs(self.jdiff(q0, q1)))) / self.vmax

    def free_move_ok(self, q0: np.ndarray, q1: np.ndarray) -> bool:
        dq = self.jdiff(q0, q1)
        for t in (0.25, 0.5, 0.75):
            q = q0 + dq * t
            if self.clearance(q) < self.p.robot.table_clearance_margin_m:
                return False
        return True

    # -- linear tracking ----------------------------------------------------------------------
    def track_linear(self, A: tuple[np.ndarray, np.ndarray], B: tuple[np.ndarray, np.ndarray], L: float, starts: list[Node], wp_index: int) -> tuple[list[Node], str, float | None, Counter]:
        (p0, R0), (p1, R1) = A, B
        dist = float(np.linalg.norm(p1 - p0))
        ang = float(np.linalg.norm(rot_log(R0.T @ R1)))
        n = max(2, int(math.ceil(dist / self.cell.sample_step_m)) + 1, int(math.ceil(ang / 0.12)) + 1)
        layer: list[tuple[np.ndarray, float, "Node | None", list, float]] = []
        # layer entries: q, cost, origin start node, chain of samples, min inv cond
        starts_q = starts
        reasons: Counter = Counter()
        for i in range(n):
            t = i / (n - 1)
            pos = p0 + (p1 - p0) * t
            R = slerp(R0, R1, t) if ang > 1e-9 else R0
            cand, why = self.ik_nodes(self.tool_T(pos, R, L), linear=True)
            if i == 0:
                # start from the already-validated nodes of the previous waypoint
                layer = []
                for s in starts_q:
                    for (q, ic, st, cl) in cand:
                        if np.max(np.abs(self.jdiff(s.q, q))) < 1e-5:
                            layer.append((q, s.cost, s, [q], ic))
                            break
                if not layer:
                    return [], "singularity" if why.get("singularity") else "branch_flip", 0.0, why or Counter({"start_not_valid_for_linear_motion": 1})
                continue
            new_layer: list[tuple[np.ndarray, float, "Node | None", list, float]] = []
            for (q, ic, st, cl) in cand:
                best = None
                for (pq, pc, origin, chain, pic) in layer:
                    if self.joint_edge_ok(pq, q):
                        c = pc + self.move_time(pq, q)
                        if best is None or c < best[0]:
                            best = (c, origin, chain, min(pic, ic))
                if best is not None:
                    new_layer.append((q, best[0], best[1], best[2] + [q], best[3]))
            if not new_layer:
                reasons.update(why)
                if not cand:
                    return [], (why.most_common(1)[0][0] if why else "no_ik_solution"), t, why
                return [], "branch_flip_or_singularity_along_line", t, why
            layer = new_layer
        ends = [Node(q=q, cost=c, parent=origin, samples=chain, wp=wp_index, inv_cond_min=ic) for (q, c, origin, chain, ic) in layer]
        return ends, "", None, reasons

    # -- full plan ------------------------------------------------------------------------------
    def evaluate(self, wps: list[Waypoint], L: float) -> PlanResult:
        results: list[WaypointResult] = []
        paths: list[list[np.ndarray]] = []
        states: list[Node] = []
        total_len = 0.0
        ic_min, stretch_max, clear_min = 1.0, 0.0, 1e9
        island_last: Node | None = None
        feasible = True
        islands_end: list[Node] = []
        prev_wp: Waypoint | None = None

        def finish_island():
            nonlocal island_last
            if island_last is not None:
                islands_end.append(island_last)
            island_last = None

        for k, wp in enumerate(wps):
            if prev_wp is not None:
                total_len += float(np.linalg.norm(wp.pos - prev_wp.pos))
            T = self.tool_T(wp.pos, wp.R, L)
            static_nodes, why = self.ik_nodes(T, linear=False)
            if wp.q_fixed is not None and static_nodes:
                static_nodes = [n for n in static_nodes if float(np.max(np.abs(wrap_to_pi(n[0] - wp.q_fixed)))) < 0.05]
            for (_, ic, st, cl) in static_nodes:
                ic_min = min(ic_min, ic)
                stretch_max = max(stretch_max, st)
                clear_min = min(clear_min, cl)
            # tool tip below table?
            if wp.pos[2] < self.table_z - 1e-6:
                results.append(WaypointResult(k, False, "below_table", f"tool point z={wp.pos[2]:.3f} m is below the table plane ({self.table_z:.3f} m)"))
                feasible = False
                finish_island()
                states = []
                prev_wp = wp
                continue
            if not static_nodes:
                reason = why.most_common(1)[0][0] if why else "no_ik_solution"
                results.append(WaypointResult(k, False, reason, self._why_text(reason, wp, L)))
                feasible = False
                finish_island()
                states = []
                prev_wp = wp
                continue

            if not states or wp.edge == "free" or prev_wp is None:
                restart = not states and prev_wp is not None
                new_states: list[Node] = []
                for (q, ic, st, cl) in static_nodes:
                    best_cost, best_parent = None, None
                    if states:
                        for s in states:
                            if self.free_move_ok(s.q, q):
                                c = s.cost + self.move_time(s.q, q)
                                if best_cost is None or c < best_cost:
                                    best_cost, best_parent = c, s
                        if best_cost is None:
                            continue
                    else:
                        best_cost, best_parent = 0.0, None
                    new_states.append(Node(q=q, cost=best_cost, parent=best_parent, samples=[q] if best_parent is None else [best_parent.q, q], wp=k, inv_cond_min=ic))
                if not new_states:
                    results.append(WaypointResult(k, False, "free_move_blocked", "no joint-space move from the previous pose is free of the table"))
                    feasible = False
                    finish_island()
                    states = []
                    prev_wp = wp
                    continue
                states = new_states
                results.append(WaypointResult(k, True, nodes=len(states)))
                if restart:
                    pass
            else:
                assert prev_wp is not None
                ends, reason, frac, why2 = self.track_linear((prev_wp.pos, prev_wp.R), (wp.pos, wp.R), L, states, k)
                if not ends:
                    detail = self._linear_text(reason, frac, why2, prev_wp, wp)
                    results.append(WaypointResult(k, False, reason, detail, fail_fraction=frac))
                    feasible = False
                    finish_island()
                    states = []
                    prev_wp = wp
                    continue
                states = ends
                for e in ends:
                    ic_min = min(ic_min, e.inv_cond_min)
                results.append(WaypointResult(k, True, nodes=len(states)))
            island_last = min(states, key=lambda s: s.cost)
            prev_wp = wp
        finish_island()
        # joint-space sample chains of each connected island
        total_time = 0.0
        for end in islands_end:
            chain: list[np.ndarray] = []
            n: Node | None = end
            seq: list[Node] = []
            while n is not None:
                seq.append(n)
                n = n.parent
            seq.reverse()
            for node in seq:
                chain.extend(node.samples)
            paths.append(chain)
            total_time += end.cost
        return PlanResult(
            feasible=feasible and bool(wps), waypoints=results, paths=paths, min_time_s=total_time,
            cartesian_length_m=total_len, inv_cond_min=ic_min, stretch_max=stretch_max, clearance_min=clear_min if clear_min < 1e8 else float("nan"),
        )

    # -- messages -----------------------------------------------------------------------------
    def _why_text(self, reason: str, wp: Waypoint, L: float) -> str:
        r = float(math.hypot(wp.pos[0], wp.pos[1]))
        z = float(wp.pos[2])
        base = f"waypoint '{wp.label}' at radius {r:.3f} m, height {z:.3f} m from the robot base (tool length {L:.2f} m)"
        return {
            "no_ik_solution": f"{base}: no joint solution exists - the point is outside the arm's reachable volume for this tool orientation",
            "joint_limit": f"{base}: every joint solution violates a joint limit (elbow is limited to +-160 deg in the manual)",
            "reach_margin": f"{base}: reachable only within {self.cell.reach_margin_m * 100:.0f} cm of the kinematic limit (too close to full stretch)",
            "table_collision": f"{base}: the arm itself would touch the table in every joint solution",
            "singularity": f"{base}: only near-singular joint solutions exist",
        }.get(reason, f"{base}: {reason}")

    def _linear_text(self, reason: str, frac: float | None, why: Counter, a: Waypoint, b: Waypoint) -> str:
        where = "at the start" if not frac else f"{frac * 100:.0f}% along the straight line"
        top = ", ".join(f"{k}x{v}" for k, v in why.most_common(3)) or reason
        return (f"straight-line move '{a.label}' -> '{b.label}' cannot be completed continuously {where}: {top}")

    # -- reach scans ----------------------------------------------------------------------------
    def radial_reach_limits(self, z: float, azimuth: float, L: float, R: np.ndarray | None = None, r_max: float = 1.1, step: float = 0.01) -> tuple[float | None, float | None]:
        """Inner / outer radius [m] around `azimuth` at height z where the tool-down pose is reachable
        (ignoring margin). Scans the radial line through the object."""
        R = tool_down_rotation() if R is None else R
        ok = []
        r = 0.0
        while r <= r_max:
            pos = np.array([r * math.cos(azimuth), r * math.sin(azimuth), z])
            sols = self.kin.ik(self.tool_T(pos, R, L))
            good = False
            for q in sols:
                if self.kin.within_limits(q) and self.clearance(q) >= self.p.robot.table_clearance_margin_m:
                    good = True
                    break
            ok.append((r, good))
            r += step
        good_r = [r for r, g in ok if g]
        if not good_r:
            return None, None
        return min(good_r), max(good_r)

    # -- payload by torque ----------------------------------------------------------------------
    def payload_budget_kg(self, qs: list[np.ndarray], cog_offset_m: float) -> tuple[float, int]:
        """Largest payload mass [kg] (CoG on the tool axis at `cog_offset_m`) for which the static gravity
        torque stays within the usable joint-torque fraction at every pose in `qs`.
        Returns (budget, index of the limiting pose); budget < 0 means even the bare arm exceeds the cap."""
        caps = np.array(self.p.robot.joint_torque_max_nm) * self.p.robot.torque_usable_fraction
        best = math.inf
        worst_i = 0
        for i, q in enumerate(qs):
            t0 = self.kin.gravity_torques(q, 0.0)
            t1 = self.kin.gravity_torques(q, 1.0, (0.0, 0.0, cog_offset_m)) - t0
            m_hi = math.inf
            for j in range(6):
                if abs(t1[j]) < 1e-9:
                    if abs(t0[j]) > caps[j]:
                        m_hi = -1.0
                    continue
                lo, hi = sorted(((-caps[j] - t0[j]) / t1[j], (caps[j] - t0[j]) / t1[j]))
                if hi < 0 or lo > hi:
                    m_hi = -1.0
                else:
                    m_hi = min(m_hi, hi)
            if m_hi < best:
                best, worst_i = m_hi, i
        return (best if best != math.inf else float("inf")), worst_i
