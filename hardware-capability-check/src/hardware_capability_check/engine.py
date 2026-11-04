"""The hardware capability check: skill.json + hardware profiles -> FeasibilityReport.

Scope (deliberately narrow): *can this hardware physically do what the skill describes?*
Not checked here: whether the task is the right one, safety certification, collision-free trajectory
planning against unknown obstacles, perception accuracy beyond camera geometry.
"""
from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict

import numpy as np

from .actions import CLOSING_GRASPS, PASSIVE_GRASPS, SUCTION_GRASPS, ActionSpec, resolve_action
from .kinematics import GRAVITY, tool_down_rotation
from .motion import Evaluator, Plan, PlanParams, PlanResult, Planner, Waypoint
from .profiles import Profiles, load_profiles
from .report import FeasibilityReport, Finding, Status, StepReport, Verdict, worst
from .skill_model import SceneObject, Skill, Step, as_float, find_value, parse_vec3

FRICTION_ASSUMED = 0.3          # rubber-on-plastic style friction used only to size grip force requirements
MIN_PIXELS_ON_TARGET = 20       # pixels across an object needed for reliable detection (rule of thumb)
MAX_SCAN_COMBINATIONS = 90
ACTUATOR_NEED = ("an actuated gripper (e.g. servo/pneumatic driven from the UR tool I/O), or a passive mode "
                 "(scoop/hook/cradle) declared in the tool profile if the tool's shape really provides it")


def _fmt_range(vals: list[float], unit: str = "m") -> str:
    if not vals:
        return "none"
    lo, hi = min(vals), max(vals)
    return f"{lo:.2f}-{hi:.2f} {unit}" if hi > lo else f"{lo:.2f} {unit}"


class Checker:
    def __init__(self, skill: Skill, profiles: Profiles):
        self.skill = skill
        self.p = profiles
        self.robot = profiles.robot
        self.tool = profiles.tool
        self.cam = profiles.camera
        self.cell = profiles.cell
        self.planner = Planner(skill, profiles)
        self.ev = Evaluator(profiles)
        self.step_findings: dict[int, list[Finding]] = defaultdict(list)
        self.global_findings: list[Finding] = []
        self.assumptions: list[str] = []
        self.metrics: dict = {}

    # ------------------------------------------------------------------------------------ helpers
    def add(self, f: Finding, step: Step | None = None) -> None:
        if step is None:
            self.global_findings.append(f)
        else:
            f.step_id = step.id
            self.step_findings[step.index].append(f)

    def step_of(self, index: int) -> Step:
        return self.skill.steps[index]

    # ------------------------------------------------------------------------------------ main
    def run(self) -> FeasibilityReport:
        sk = self.skill
        self.check_skill_file()
        self.check_cell()
        specs = {s.index: resolve_action(s.action) for s in sk.steps}
        self.check_tool_steps(specs)
        self.check_vision(specs)
        from .robot_claims import check_robot_claims
        check_robot_claims(self)
        self.check_motion(specs)
        self.check_step_requirements(specs)
        self.check_timing()
        self.check_template(specs)
        return self.assemble(specs)

    def check_template(self, specs) -> None:
        from .template_coverage import coverage
        from .template_flags import check_flags
        from .template_objects import check_object_constraints
        from .template_verify import check_failure_policies, check_success_criteria, check_verify_params
        check_flags(self, specs)
        check_object_constraints(self, specs)
        check_verify_params(self, specs)
        check_failure_policies(self, specs)
        check_success_criteria(self, specs)
        cov = coverage(self.skill.raw)
        self.metrics["template_fields_evaluated"] = cov["evaluated_fields"]
        self.metrics["template_fields_descriptive_only"] = cov["descriptive_only_fields"]
        if cov["not_evaluated_fields"]:
            self.add(Finding("template_coverage", Status.INFO, f"{len(cov['not_evaluated_fields'])} field(s) are not understood and were not evaluated: " + ", ".join(cov["not_evaluated_fields"][:8]), basis="unknown"))

    # ------------------------------------------------------------------------------------ skill file / cell
    def check_skill_file(self) -> None:
        sk = self.skill
        if not sk.steps:
            self.add(Finding("skill_file", Status.UNKNOWN, "the skill has no executable steps", needs=["a non-empty 'steps' list"], basis="unknown"))
        for issue in sk.issues:
            st = Status.CONDITIONAL if "assuming meters" in issue else Status.UNKNOWN
            cond = ["skill lengths are in metres"] if "assuming meters" in issue else []
            self.add(Finding("skill_file", st, issue, conditions=cond, basis="assumed"))
        for o in sk.objects.values():
            if o.position is None:
                self.add(Finding("scene", Status.UNKNOWN, f"object '{o.id}' has no stated position", needs=[f"position of '{o.id}'"], basis="unknown"))

    def check_cell(self) -> None:
        c = self.cell
        if c.base_status != "confirmed":
            self.assumptions.append(f"robot base at {np.round(c.base_position_m, 3).tolist()} m, yaw {math.degrees(c.base_yaw_rad):.0f} deg in the skill's world frame (cell.robot_base_in_world: {c.base_status})")
        if c.table_status != "confirmed":
            self.assumptions.append(f"table surface at z = {c.table_z_m:.3f} m (cell.table.surface_z_m: {c.table_status})")
        if c.mounting_status != "confirmed":
            self.assumptions.append(f"robot mounted {c.mounting} (cell.mounting: {c.mounting_status})")
        if c.mounting != "upright":
            self.add(Finding("mounting", Status.UNKNOWN, f"robot mounting '{c.mounting}' is not modelled (gravity/table checks assume an upright base)",
                             needs=["upright mounting, or extend the checker for wall/ceiling mounting"], basis="unknown"))
        # TWO CAMERAS: `self.cam` (D415) is the computer-vision camera of the execution cell and the only one the
        # verdict is computed against. The D435 recorded the demonstration that became skill.json; it is reported
        # here for traceability only. It is still unsure whether skill.json will be created with both cameras or
        # one - that choice does not change any pass/fail result in this checker.
        demo = self.p.demo_camera
        if demo is not None:
            self.assumptions.append(
                f"two cameras: '{self.cam.name}' = computer vision (used for DETECT/VERIFY coverage); '{demo.name}' recorded the demonstration "
                "(not evaluated). Whether skill.json is created from both cameras or only one is undecided"
            )
        self.assumptions.append("nominal UR DH kinematics (per-robot calibration offsets are not available; positions are +-few mm)")
        self.assumptions.append("arm link radii for the table-clearance check are approximate (UR publishes none)")
        if not self.skill.frame_known:
            self.assumptions.append("skill coordinate frame not recognised; treated as the world frame")

    # ------------------------------------------------------------------------------------ tool capability
    def grasp_style(self, step: Step) -> str | None:
        g = find_value(step.params, ("grasp_type", "grasp", "hold_mode", "gripper_mode", "grip_type"), max_depth=2)
        return None if g is None else str(g).strip().lower().replace(" ", "_").replace("-", "_")

    def check_tool_steps(self, specs: dict[int, ActionSpec | None]) -> None:
        tool = self.tool
        for s in self.skill.steps:
            spec = specs[s.index]
            if spec is None:
                continue
            obj = self.skill.object_by_ref(s.target)
            if spec.needs_hold:
                self.add(self.hold_finding(s, obj), s)
            release_flag = find_value(s.params, ("release_after_placement", "release", "release_after"), max_depth=2)
            if spec.needs_release and release_flag is not False:
                self.add(self.release_finding(s), s)
            if spec.canonical == "ROTATE":
                self.add(self.rotate_finding(s), s)

    def hold_finding(self, step: Step, obj: SceneObject | None) -> Finding:
        tool = self.tool
        style = self.grasp_style(step)
        src = [f"tool:{tool.id}"]
        if tool.actuated:
            conds: list[str] = []
            needs: list[str] = []
            evidence: dict = {"tool_actuation": tool.actuation_kind}
            width = None
            if obj is not None and obj.size is not None:
                width = float(np.min(obj.size[:2]))
                evidence["object_min_width_m"] = round(width, 4)
                evidence["object_size_basis"] = obj.size_basis
            if tool.opening_max_m is not None and width is not None:
                evidence["tool_opening_max_m"] = tool.opening_max_m
                if width > tool.opening_max_m:
                    return Finding("grasp", Status.FAIL, f"object width {width * 1000:.0f} mm exceeds the tool opening {tool.opening_max_m * 1000:.0f} mm", evidence=evidence, sources=src, basis="computed")
            else:
                conds.append("tool opening >= object width" + ("" if width is None else f" ({width * 1000:.0f} mm)"))
                needs.append("tool opening range" if tool.opening_max_m is None else "object width")
            if obj is not None and obj.mass_kg is not None and tool.grip_force_n is not None:
                need_f = obj.mass_kg * GRAVITY / (2 * FRICTION_ASSUMED)
                evidence["required_grip_force_n"] = round(need_f, 2)
                if tool.grip_force_n < need_f:
                    return Finding("grasp", Status.FAIL, f"grip force {tool.grip_force_n:.1f} N is below the {need_f:.1f} N needed to hold {obj.mass_kg:.3f} kg (friction {FRICTION_ASSUMED})", evidence=evidence, sources=src, basis="computed")
            else:
                conds.append("grip force sufficient for the object's weight")
                needs.append("grip force of the tool and object mass")
            if not tool.control_interface:
                conds.append("tool actuation wired to the UR tool I/O (2 digital outputs, 12/24 V, 1.5 A) or controller I/O")
                needs.append("how the tool is actuated (tool_interface)")
            st = Status.PASS if not conds else Status.CONDITIONAL
            return Finding("grasp", st, "tool can close on the object" if st == Status.PASS else "tool is actuated; grasp feasible if the listed values hold",
                           evidence=evidence, conditions=conds, needs=needs, sources=src, basis="computed")
        # rigid tool ------------------------------------------------------------------------
        if style in SUCTION_GRASPS:
            return Finding("grasp", Status.FAIL, f"'{style}' needs a vacuum source; the tool has none", sources=src, basis="documented",
                           needs=["a vacuum/suction tool or change the grasp type"])
        if style in PASSIVE_GRASPS or (style is None and tool.passive_modes):
            mode = style if style in PASSIVE_GRASPS else tool.passive_modes[0]
            if mode in tool.passive_modes:
                return Finding("grasp", Status.CONDITIONAL, f"rigid tool can hold the object by shape ('{mode}') - only if the geometry really captures it",
                               conditions=[f"the tool geometry captures the object by '{mode}' (needs tool dimensions + object size)"],
                               needs=["tool CAD/dimensions and object size"], sources=src, basis="assumed")
            return Finding("grasp", Status.FAIL, f"grasp style '{style}' is not among the tool's passive modes {tool.passive_modes or '[]'}", sources=src, basis="documented",
                           needs=["a tool with this capability, or declare the mode in the tool profile if its shape supports it"])
        style_txt = f"'{style}' grasp" if style else "holding the object"
        return Finding("grasp", Status.FAIL,
                       f"{style_txt} needs a tool that can close, but '{tool.name}' is rigid (actuated: false) and declares no passive holding mode",
                       sources=src, basis="documented",
                       needs=[ACTUATOR_NEED])

    def release_finding(self, step: Step) -> Finding:
        tool = self.tool
        src = [f"tool:{tool.id}"]
        if tool.actuated:
            return Finding("release", Status.PASS if tool.control_interface else Status.CONDITIONAL,
                           "tool can open to release the object" if tool.control_interface else "tool is actuated; release works if its actuation is wired to the controller",
                           conditions=[] if tool.control_interface else ["tool actuation wired to the UR tool I/O"], sources=src, basis="computed")
        passive = [m for m in tool.passive_modes if m in ("scoop", "cradle")]
        if passive:
            return Finding("release", Status.CONDITIONAL, f"a rigid '{passive[0]}' tool can only release by tilting; wrist rotation is available",
                           conditions=["the object slides off when the tool is tilted (friction/geometry validated physically)"], sources=src, basis="assumed")
        return Finding("release", Status.FAIL, f"'{tool.name}' is rigid: there is no mechanism to let go of a held object", sources=src, basis="documented",
                       needs=[ACTUATOR_NEED])

    def rotate_finding(self, step: Step) -> Finding:
        ang = as_float(find_value(step.params, ("angle_deg", "angle", "degrees"), max_depth=1))
        lim = math.degrees(self.robot.joint_max_rad[5] - self.robot.joint_min_rad[5])
        if ang is None:
            cont = find_value(step.params, ("continuous", "unlimited", "endless"), max_depth=1)
            if cont is True:
                return Finding("wrist_rotation", Status.FAIL, "continuous rotation requested; the robot's wrist 3 is limited to +-360 deg per the robot profile", sources=["ur5e:limits.joint_position_deg"], basis="documented")
            return Finding("wrist_rotation", Status.UNKNOWN, "ROTATE without an angle", needs=["rotation angle in degrees"], basis="unknown")
        if abs(ang) <= lim:
            return Finding("wrist_rotation", Status.PASS, f"{ang:g} deg rotation fits wrist 3's total travel of {lim:g} deg", evidence={"angle_deg": ang, "wrist3_travel_deg": lim}, sources=["ur5e:limits.joint_position_deg"], basis="documented")
        return Finding("wrist_rotation", Status.FAIL, f"{ang:g} deg exceeds wrist 3's total travel of {lim:g} deg", evidence={"angle_deg": ang, "wrist3_travel_deg": lim}, sources=["ur5e:limits.joint_position_deg"], basis="documented")

    # ------------------------------------------------------------------------------------ vision
    def vision_objects(self, specs: dict[int, ActionSpec | None]) -> list[SceneObject]:
        wanted: dict[str, SceneObject] = {}
        flags = self.skill.execution_flags
        for o in self.skill.objects.values():
            det = o.raw.get("detection") if isinstance(o.raw.get("detection"), dict) else {}
            if det.get("required") or det.get("reidentify_before_pick") or det.get("reidentify_before_place"):
                wanted[o.id] = o
        for s in self.skill.steps:
            spec = specs[s.index]
            if spec and spec.needs_vision:
                o = self.skill.object_by_ref(s.target)
                if o is not None:
                    wanted[o.id] = o
        if flags.get("require_object_reidentification"):
            for s in self.skill.steps:
                spec = specs[s.index]
                if spec and spec.canonical in ("PICK", "PLACE"):
                    o = self.skill.object_by_ref(s.target)
                    if o is not None:
                        wanted[o.id] = o
        return list(wanted.values())

    def needs_vision(self, specs: dict[int, ActionSpec | None]) -> bool:
        return bool(self.vision_objects(specs)) or any(sp and sp.needs_vision for sp in specs.values())

    def check_vision(self, specs: dict[int, ActionSpec | None]) -> None:
        if not self.needs_vision(specs):
            return
        # Computer-vision camera of the execution cell (Intel RealSense D415). NOT the D435 that recorded the
        # demonstration: that one is informational and may or may not also be used to create skill.json (undecided).
        cam = self.cam
        objs = self.vision_objects(specs)
        res_x = cam.resolution_px[0]
        tan_h = math.tan(math.radians(cam.fov_h_deg) / 2)
        tan_v = math.tan(math.radians(cam.fov_v_deg) / 2)
        lo, hi = cam.ideal_range_m
        sources = [f"camera:{cam.id}"]
        findings: list[Finding] = []
        mount = cam.mount
        for o in objs:
            ev: dict = {"camera": cam.name, "ideal_range_m": [lo, hi], "fov_deg": [cam.fov_h_deg, cam.fov_v_deg]}
            if o.size is not None:
                width = float(np.min(o.size[:2])) if not o.is_container else float(np.max(o.size[:2]))
                mm_per_px_at_1m = 2 * tan_h / res_x * 1000.0
                max_standoff = width / (MIN_PIXELS_ON_TARGET * 2 * tan_h / res_x)
                ev.update({"object_width_m": round(width, 4), "object_size_basis": o.size_basis,
                           "pixels_across_object_at_0.5m": round(width / (0.5 * 2 * tan_h / res_x), 1),
                           "max_standoff_for_20px_m": round(max_standoff, 2), "depth_mm_per_pixel_at_1m": round(mm_per_px_at_1m, 2)})
                usable_hi = min(hi, max_standoff)
                if usable_hi < lo:
                    findings.append(Finding("vision", Status.FAIL, f"'{o.id}' ({width * 1000:.0f} mm) cannot be resolved with >= {MIN_PIXELS_ON_TARGET} px at the camera's {lo} m minimum range", evidence=ev, sources=sources, basis="computed"))
                    continue
                ev["usable_standoff_m"] = [lo, round(usable_hi, 2)]
            else:
                usable_hi = hi
            if mount is None:
                visible_area = (2 * lo * tan_h, 2 * lo * tan_v)
                ev["view_area_at_min_range_m"] = [round(visible_area[0], 2), round(visible_area[1], 2)]
                findings.append(Finding("vision", Status.CONDITIONAL,
                                        f"camera can detect '{o.id}' from {lo}-{usable_hi:.2f} m; coverage cannot be confirmed because the camera mounting is unknown",
                                        evidence=ev, conditions=[f"camera mounted {lo}-{usable_hi:.2f} m from '{o.id}' with it inside the {cam.fov_h_deg:g} x {cam.fov_v_deg:g} deg view"],
                                        needs=["camera mounting (fixed pose or eye-in-hand offset)"], sources=sources, basis="computed"))
                continue
            findings.append(self.coverage_finding(o, mount, ev, usable_hi, sources))
        # attach: one finding per object to the first vision step, others global
        vision_steps = [s for s in self.skill.steps if specs[s.index] and specs[s.index].needs_vision]
        for f in findings:
            if vision_steps:
                tgt = next((s for s in vision_steps if self.skill.object_by_ref(s.target) and f"'{self.skill.object_by_ref(s.target).id}'" in f.message), vision_steps[0])
                self.add(f, tgt)
            else:
                self.add(f)
        if not objs and vision_steps:
            for s in vision_steps:
                self.add(Finding("vision", Status.CONDITIONAL, "camera is present; the vision step names no object to evaluate", conditions=["objects visible to the camera"], sources=sources, basis="assumed"), s)

    def coverage_finding(self, o: SceneObject, mount: dict, ev: dict, usable_hi: float, sources: list[str]) -> Finding:
        cam = self.cam
        lo, hi = cam.ideal_range_m
        if o.position is None:
            return Finding("vision", Status.UNKNOWN, f"'{o.id}' has no position, coverage cannot be tested", needs=[f"position of '{o.id}'"], evidence=ev, sources=sources)
        mtype = str(mount.get("type", "")).lower()
        if mtype == "fixed":
            cp = parse_vec3(mount.get("position_m"))
            look = parse_vec3(mount.get("look_at_m"))
            if cp is None or look is None:
                return Finding("vision", Status.UNKNOWN, "camera mount 'fixed' needs position_m and look_at_m", needs=["camera position and look-at point"], sources=sources)
            z = look - cp
            z = z / np.linalg.norm(z)
            x = np.cross(z, np.array([0.0, 0.0, 1.0]))
            if np.linalg.norm(x) < 1e-6:
                x = np.array([1.0, 0.0, 0.0])
            x /= np.linalg.norm(x)
            y = np.cross(z, x)
            v = o.position - cp
            depth = float(v @ z)
            if depth <= 0:
                return Finding("vision", Status.FAIL, f"'{o.id}' is behind the camera", evidence=ev, sources=sources, basis="computed")
            ah = math.degrees(math.atan2(float(v @ x), depth))
            av = math.degrees(math.atan2(float(v @ y), depth))
            rng = float(np.linalg.norm(v))
            ev.update({"range_m": round(rng, 3), "angle_h_deg": round(ah, 1), "angle_v_deg": round(av, 1)})
            if abs(ah) > cam.fov_h_deg / 2 or abs(av) > cam.fov_v_deg / 2:
                return Finding("vision", Status.FAIL, f"'{o.id}' is outside the camera's field of view ({ah:.0f} deg, {av:.0f} deg off axis)", evidence=ev, sources=sources, basis="computed")
            if rng < lo or rng > min(hi, usable_hi):
                return Finding("vision", Status.FAIL, f"'{o.id}' is {rng:.2f} m from the camera; usable range is {lo}-{min(hi, usable_hi):.2f} m", evidence=ev, sources=sources, basis="computed")
            return Finding("vision", Status.PASS, f"'{o.id}' is inside the view cone at {rng:.2f} m", evidence=ev, sources=sources, basis="computed")
        if mtype == "eye_in_hand":
            off = parse_vec3(mount.get("offset_from_flange_m"))
            off = np.zeros(3) if off is None else off
            standoff = max(lo + 0.1, 0.6)
            target = self.planner.to_base(o.position) if self.skill.frame != "base" else o.position
            R = tool_down_rotation()
            flange = np.array([target[0], target[1], target[2] + standoff + off[2]]) - R @ np.array([off[0], off[1], 0.0])
            T = np.eye(4)
            T[:3, :3] = R
            T[:3, 3] = flange
            nodes, why = self.ev.ik_nodes(T, linear=False)
            ev["observation_standoff_m"] = standoff
            if nodes:
                return Finding("vision", Status.PASS, f"arm can hold the camera {standoff:.2f} m above '{o.id}' looking down", evidence=ev, sources=sources, basis="computed")
            return Finding("vision", Status.FAIL, f"arm cannot place the camera {standoff:.2f} m above '{o.id}' ({why.most_common(1)[0][0] if why else 'no solution'})", evidence=ev, sources=sources, basis="computed")
        return Finding("vision", Status.UNKNOWN, f"unknown camera mount type '{mtype}'", needs=["camera mount type: fixed | eye_in_hand"], sources=sources)

    # ------------------------------------------------------------------------------------ motion
    def tool_lengths(self) -> list[float]:
        if self.tool.tcp_offset_m is not None:
            return [self.tool.tcp_offset_m]
        lo, hi = self.tool.tcp_offset_search_m
        n = 6
        return [round(lo + (hi - lo) * i / (n - 1), 4) for i in range(n)]

    def check_motion(self, specs: dict[int, ActionSpec | None]) -> None:
        sk, cell = self.skill, self.cell
        lengths = self.tool_lengths()
        length_known = self.tool.tcp_offset_m is not None
        base_L = lengths[0]
        probe = self.planner.build(PlanParams(base_L, None, None))
        for issue in probe.issues:
            st = self.step_of(issue.step_index)
            self.add(Finding(issue.check, Status.UNKNOWN if issue.status == "unknown" else Status.FAIL, issue.message, needs=issue.needs, basis="unknown"), st)
        if not probe.waypoints:
            return
        heights: list[float | None] = cell.scan_container_height_m if probe.unknown_container_height else [None]
        sizes: list[float | None] = cell.scan_object_size_m if probe.unknown_object_size else [None]
        combos = list(itertools.product(lengths, heights, sizes))[:MAX_SCAN_COMBINATIONS]
        results: list[tuple[PlanParams, Plan, PlanResult]] = []
        for L, h, sz in combos:
            params = PlanParams(L, h, sz)
            plan = self.planner.build(params)
            results.append((params, plan, self.ev.evaluate(plan.waypoints, L)))
        n_wp = len(probe.waypoints)
        reached = np.zeros(n_wp)
        reasons: dict[int, Counter] = defaultdict(Counter)
        details: dict[int, str] = {}
        for params, plan, res in results:
            for wr in res.waypoints:
                if wr.reached:
                    reached[wr.index] += 1
                else:
                    reasons[wr.index][wr.reason] += 1
                    details.setdefault(wr.index, wr.detail)
        total = len(results)
        feasible_runs = [r for r in results if r[2].feasible]
        feasible_L = sorted({r[0].tcp_offset_m for r in feasible_runs})
        # also lengths that are feasible for EVERY scanned height/size
        robust_L = sorted(L for L in lengths if all(r[2].feasible for r in results if r[0].tcp_offset_m == L))
        # representative run
        if feasible_runs:
            mid = feasible_L[len(feasible_L) // 2]
            rep = next(r for r in feasible_runs if r[0].tcp_offset_m == mid)
        else:
            rep = max(results, key=lambda r: sum(1 for w in r[2].waypoints if w.reached))
        rep_params, rep_plan, rep_res = rep
        scanned_unknowns = (not length_known) or probe.unknown_container_height or probe.unknown_object_size
        scan_desc = []
        if not length_known:
            scan_desc.append(f"tool length {_fmt_range(lengths)}")
        if probe.unknown_container_height:
            scan_desc.append(f"container wall height {_fmt_range([h for h in heights if h is not None])}")
        if probe.unknown_object_size:
            scan_desc.append(f"object size {_fmt_range([s for s in sizes if s is not None])}")
        if scan_desc:
            self.assumptions.append("unknown values were scanned, not assumed: " + "; ".join(scan_desc))
        if not length_known:
            self.assumptions.append(f"tool length (flange to contact point) unknown: scanned {_fmt_range(lengths)} (tool.tcp_offset_search_m)")

        # --- per step results
        by_step: dict[int, list[int]] = defaultdict(list)
        for k, w in enumerate(rep_plan.waypoints):
            by_step[w.step_index].append(k)
        for step_index, ks in by_step.items():
            step = self.step_of(step_index)
            frac = [reached[k] / total for k in ks]
            labels = ", ".join(rep_plan.waypoints[k].label for k in ks)
            ev = {"waypoints": labels, "scenarios_tested": total}
            if min(frac) >= 1.0:
                if scanned_unknowns:
                    conds = []
                    if not length_known:
                        conds.append(f"tool length (flange to contact point) within {_fmt_range(lengths)}")
                    spec_s = resolve_action(step.action)
                    if probe.unknown_container_height and spec_s is not None and spec_s.canonical == "PLACE":
                        conds.append(f"container wall height within {_fmt_range([h for h in heights if h is not None])}")
                    if probe.unknown_object_size:
                        conds.append(f"object size within {_fmt_range([s for s in sizes if s is not None])}")
                    self.add(Finding("reach_and_path", Status.CONDITIONAL, f"all waypoints ({labels}) are reachable with continuous joint motion for every scanned value of the unknowns",
                                     evidence=ev, conditions=conds, sources=["ur5e:kinematics", "ur5e:limits.joint_position_deg"], basis="computed"), step)
                else:
                    self.add(Finding("reach_and_path", Status.PASS, f"all waypoints ({labels}) are reachable with continuous joint motion", evidence=ev,
                                     sources=["ur5e:kinematics", "ur5e:limits.joint_position_deg"], basis="computed"), step)
            elif min(frac) <= 0.0:       # at least one waypoint is unreachable in every scenario
                k_bad = next(k for k in ks if reached[k] == 0)
                reason = reasons[k_bad].most_common(1)[0][0] if reasons[k_bad] else "unreachable"
                ev["failure_reason"] = reason
                self.add(Finding("reach_and_path", Status.FAIL, details.get(k_bad, "unreachable"), evidence=ev, sources=["ur5e:kinematics", "ur5e:limits.joint_position_deg"], basis="computed",
                                 needs=self.reach_remedy(reason)), step)
            else:
                ev["fraction_of_scenarios_reachable"] = round(min(frac), 2)
                ev["feasible_tool_lengths_m"] = feasible_L
                self.add(Finding("reach_and_path", Status.CONDITIONAL, f"reachable only for part of the scanned unknowns (tool length {_fmt_range(feasible_L)})",
                                 evidence=ev, conditions=[f"tool length (flange to contact point) within {_fmt_range(feasible_L)}"],
                                 sources=["ur5e:kinematics"], basis="computed"), step)

        # --- reach margins for picking / placing targets
        self.reach_margin_findings(rep_plan, rep_params)

        # --- whole-plan metrics
        self.metrics["tool_lengths_scanned_m"] = _fmt_range(lengths) if not length_known else f"{lengths[0]:.2f} (given)"
        self.metrics["feasible_tool_lengths_m"] = _fmt_range(feasible_L) if feasible_L else "none"
        if robust_L:
            self.metrics["tool_lengths_robust_to_all_unknowns_m"] = _fmt_range(robust_L)
        self.metrics["scenarios_tested"] = f"{len(feasible_runs)}/{total} fully feasible"
        self.metrics["min_arm_time_s (joint-speed lower bound, no acceleration limits)"] = round(rep_res.min_time_s, 2)
        self.metrics["tool_path_length_m"] = round(rep_res.cartesian_length_m, 2)
        self.metrics["time_at_teach-mode_speed_0.25m/s_s"] = round(rep_res.cartesian_length_m / 0.25, 1)
        if not math.isnan(rep_res.clearance_min):
            self.metrics["min_arm_to_table_clearance_m (approx. link radii)"] = round(rep_res.clearance_min, 3)
        self.metrics["max_arm_stretch (1.0 = fully extended)"] = round(rep_res.stretch_max, 2)
        self.metrics["min_inverse_condition_number (0 = singular)"] = round(rep_res.inv_cond_min, 3)
        if rep_res.inv_cond_min < cell.sing_warn:
            self.add(Finding("singularity", Status.CONDITIONAL, f"path passes close to a singularity (condition {rep_res.inv_cond_min:.3f} < {cell.sing_warn})", conditions=["slow motion near the singular region"], basis="computed"))

        # --- primitive sequences (template schema 1.1)
        from .sequence_checks import check_sequences
        self.seq_log_all = rep_plan.seq_log
        check_sequences(self, rep_plan, rep_params)

        # --- payload / torque
        self.check_payload(rep_plan, rep_params, rep_res, specs)

    def reach_remedy(self, reason: str) -> list[str]:
        return {
            "no_ik_solution": ["move the object/box closer to the robot base (within ~0.85 m and outside the cylinder above/below the base), or use a different robot"],
            "joint_limit": ["a pose that avoids the joint limit (elbow +-160 deg), e.g. move the target away from the base"],
            "reach_margin": ["move the target away from the kinematic limit"],
            "table_collision": ["a shorter tool or a target position away from the table edge of reach"],
            "singularity": ["a target position away from the base axis"],
            "below_table": ["a target height at or above the table surface"],
        }.get(reason, [])

    def reach_margin_findings(self, plan: Plan, params: PlanParams) -> None:
        L = params.tcp_offset_m
        done = set()
        for k in plan.hold_points + plan.release_points:
            w = plan.waypoints[k]
            key = (round(float(w.pos[0]), 3), round(float(w.pos[1]), 3), round(float(w.pos[2]), 3))
            if key in done:
                continue
            done.add(key)
            az = math.atan2(w.pos[1], w.pos[0])
            r = float(math.hypot(w.pos[0], w.pos[1]))
            r_lo, r_hi = self.ev.radial_reach_limits(float(w.pos[2]), az, L, w.R)
            step = self.step_of(w.step_index)
            step_obj = step
            if r > self.robot.reach_m:
                self.add(Finding("documented_reach", Status.CONDITIONAL,
                                 f"{w.label} point is {r:.3f} m from the base axis, beyond the manufacturer's stated reach of {self.robot.reach_m:g} m",
                                 evidence={"radius_m": round(r, 3), "documented_reach_m": self.robot.reach_m},
                                 conditions=[f"operation outside the documented {self.robot.reach_m:g} m reach is acceptable (payload/accuracy not guaranteed there)"],
                                 sources=["ur5e:limits.reach_m"], basis="documented"), step_obj)
            if r_lo is None:
                continue
            self.add(Finding("reach_margin", Status.INFO,
                             f"{w.label} point is {r:.3f} m from the base axis; tool-down reachable band at z={w.pos[2]:.3f} m is {r_lo:.2f}-{r_hi:.2f} m (margin {min(r - r_lo, r_hi - r) * 100:.0f} cm to the nearest limit)",
                             evidence={"radius_m": round(r, 3), "reachable_radius_band_m": [round(r_lo, 3), round(r_hi, 3)], "tool_length_m": L},
                             sources=["ur5e:kinematics"], basis="computed"), step)

    # ------------------------------------------------------------------------------------ payload
    def check_payload(self, plan: Plan, params: PlanParams, res: PlanResult, specs: dict[int, ActionSpec | None]) -> None:
        L = params.tcp_offset_m
        tool_len_known = self.tool.tcp_offset_m is not None
        cog = self.tool.cog_offset_m if self.tool.cog_offset_m is not None else L
        rated = self.robot.rated_payload_kg(cog)
        qs: list[np.ndarray] = []
        for path in res.paths:
            qs.extend(path[::2] if len(path) > 40 else path)
        torque_budget = float("inf")
        if qs:
            torque_budget, worst_i = self.ev.payload_budget_kg(qs, cog)
        budget = min(rated, torque_budget)
        arm_only_ok = torque_budget >= 0
        # who carries what
        carried = list(plan.carried.values())
        masses_known = self.tool.mass_kg is not None and all(o.mass_kg is not None for o in carried)
        total_known = (self.tool.mass_kg or 0.0) + sum((o.mass_kg or 0.0) for o in carried)
        ev = {
            "rated_payload_at_cog_offset_kg": round(rated, 2),
            "cog_offset_m": round(cog, 3),
            "torque_limited_budget_kg": None if math.isinf(torque_budget) else round(torque_budget, 2),
            "payload_budget_kg": round(budget, 2),
            "joint_torque_usable_fraction": self.robot.torque_usable_fraction,
            "tool_mass_kg": self.tool.mass_kg,
            "carried_masses_kg": {o.id: o.mass_kg for o in carried},
        }
        src = ["ur5e:limits.payload_vs_cog_offset", "ur5e:limits.joint_torque_max_nm", "ur5e:kinematics.link_masses_kg"]
        self.metrics["payload_budget_kg (tool + carried object)"] = round(budget, 2)
        self.metrics["rated_payload_kg_at_cog_offset"] = round(rated, 2)
        if not arm_only_ok:
            self.add(Finding("payload", Status.FAIL, "even the bare arm's static gravity torque exceeds the usable joint torque at some pose on the path", evidence=ev, sources=src, basis="computed"))
            return
        if budget <= 0:
            self.add(Finding("payload", Status.FAIL, f"no payload margin at CoG offset {cog:.2f} m", evidence=ev, sources=src, basis="computed"))
            return
        if masses_known:
            ev["total_payload_kg"] = round(total_known, 3)
            if total_known > budget:
                self.add(Finding("payload", Status.FAIL, f"tool + object = {total_known:.2f} kg exceeds the payload budget {budget:.2f} kg", evidence=ev, sources=src, basis="computed"))
            else:
                st = Status.PASS if tool_len_known or self.tool.cog_offset_m is not None else Status.CONDITIONAL
                self.add(Finding("payload", st, f"tool + object = {total_known:.2f} kg is within the payload budget {budget:.2f} kg", evidence=ev, sources=src, basis="computed",
                                 conditions=[] if st == Status.PASS else [f"tool length within {_fmt_range(self.tool_lengths())}"]), None)
            return
        missing = []
        if self.tool.mass_kg is None:
            missing.append("tool mass")
        missing += [f"mass of '{o.id}'" for o in carried if o.mass_kg is None]
        self.add(Finding("payload", Status.CONDITIONAL,
                         f"the arm can carry up to {budget:.2f} kg (tool + object) along this path; actual masses are not stated",
                         evidence=ev, conditions=[f"tool mass + carried object mass <= {budget:.2f} kg"] ,
                         needs=missing, sources=src, basis="computed"))

    # ------------------------------------------------------------------------------------ per-step parameter requirements
    def check_step_requirements(self, specs: dict[int, ActionSpec | None]) -> None:
        r = self.robot
        for s in self.skill.steps:
            spec = specs[s.index]
            if spec is None:
                self.add(Finding("action", Status.UNKNOWN, f"action '{s.action}' is not a known robot action; its hardware needs cannot be evaluated",
                                 needs=[f"definition of '{s.action}' (target pose, tool use)"], basis="unknown"), s)
                continue
            # force
            fv = as_float(find_value(s.params, ("force_n", "force", "contact_force", "push_force", "pressing_force"), max_depth=2))
            if fv is not None:
                ev = {"required_force_n": fv, "ft_sensor_range_n": r.ft_range_n, "ft_accuracy_n": r.ft_accuracy_n}
                if fv > r.ft_range_n:
                    self.add(Finding("force", Status.CONDITIONAL, f"{fv:g} N is above the force/torque sensor range (+-{r.ft_range_n:g} N): force cannot be measured or controlled in closed loop", evidence=ev,
                                     conditions=["open-loop force (not measured by the built-in sensor)"], sources=["ur5e:limits.force_torque_sensor"], basis="documented"), s)
                elif fv < r.ft_accuracy_n:
                    self.add(Finding("force", Status.CONDITIONAL, f"{fv:g} N is below the force sensor accuracy (+-{r.ft_accuracy_n:g} N)", evidence=ev,
                                     conditions=["force regulated without sensor feedback"], sources=["ur5e:limits.force_torque_sensor"], basis="documented"), s)
                else:
                    self.add(Finding("force", Status.PASS, f"{fv:g} N is within the force/torque sensor range (+-{r.ft_range_n:g} N, accuracy +-{r.ft_accuracy_n:g} N)", evidence=ev,
                                     sources=["ur5e:limits.force_torque_sensor"], basis="documented"), s)
            # tolerance
            tol_m = None
            for names, factor in ((("tolerance_mm", "position_tolerance_mm", "precision_mm", "accuracy_mm"), 0.001),
                                  (("tolerance_m", "position_tolerance_m", "precision_m", "accuracy_m"), 1.0),
                                  (("tolerance", "position_tolerance", "precision", "accuracy"), self.skill.unit_scale)):
                v = as_float(find_value(s.params, names, max_depth=2))
                if v is not None:
                    tol_m = v * factor
                    break
            if tol_m is not None:
                rep = r.repeatability_mm / 1000.0
                ev = {"required_tolerance_mm": round(tol_m * 1000, 3), "arm_repeatability_mm": r.repeatability_mm}
                if tol_m < rep:
                    self.add(Finding("precision", Status.FAIL, f"required tolerance {tol_m * 1000:.3f} mm is tighter than the arm's repeatability +-{r.repeatability_mm} mm", evidence=ev, sources=["ur5e:limits.pose_repeatability_mm"], basis="documented"), s)
                elif tol_m < 0.001:
                    self.add(Finding("precision", Status.CONDITIONAL, f"{tol_m * 1000:.2f} mm is near repeatability; absolute accuracy is not specified by UR and needs per-robot calibration", evidence=ev,
                                     conditions=["calibrated robot and measured tool/part geometry"], sources=["ur5e:limits.pose_repeatability_mm"], basis="documented"), s)
                else:
                    self.add(Finding("precision", Status.PASS, f"arm repeatability +-{r.repeatability_mm} mm is far inside the required {tol_m * 1000:.2f} mm (the camera and calibration dominate the error)", evidence=ev,
                                     sources=["ur5e:limits.pose_repeatability_mm"], basis="documented"), s)
            # speed
            sp = None
            for names, factor in ((("speed_m_s", "max_speed_m_s", "tcp_speed_m_s", "velocity_m_s"), 1.0), (("speed_mm_s", "tcp_speed_mm_s"), 0.001), (("speed", "max_speed", "velocity", "tcp_speed"), self.skill.unit_scale)):
                v = as_float(find_value(s.params, names, max_depth=2))
                if v is not None:
                    sp = v * factor
                    break
            if sp is not None:
                ev = {"required_tcp_speed_m_s": sp, "arm_max_tcp_speed_m_s": r.tcp_speed_max_m_s}
                if sp > r.tcp_speed_max_m_s:
                    self.add(Finding("speed", Status.FAIL, f"{sp:g} m/s exceeds the arm's maximum TCP speed {r.tcp_speed_max_m_s:g} m/s", evidence=ev, sources=["ur5e:limits.tcp_speed_max_m_s"], basis="documented"), s)
                else:
                    self.add(Finding("speed", Status.PASS, f"{sp:g} m/s is within the arm's maximum TCP speed {r.tcp_speed_max_m_s:g} m/s (controller safety limits may be lower)", evidence=ev, sources=["ur5e:limits.tcp_speed_max_m_s"], basis="documented"), s)
            # container fit
            if spec.canonical == "PLACE":
                self.container_fit(s)

    def container_fit(self, step: Step) -> None:
        tgt = self.skill.object_by_ref(step.target)
        if tgt is None or not tgt.is_container:
            return
        held = None
        for st in self.skill.steps:
            sp = resolve_action(st.action)
            if st.index < step.index and sp and sp.canonical == "PICK":
                held = self.skill.object_by_ref(st.target)
        if held is None:
            return
        inner = tgt.inner_size if tgt.inner_size is not None else None
        if held.size is None:
            self.add(Finding("container_fit", Status.UNKNOWN, f"size of '{held.id}' is unknown", needs=[f"dimensions of '{held.id}'"], basis="unknown"), step)
            return
        w = float(max(held.size[:2]))
        margin = 2 * 0.005   # 5 mm on each side for the arm's absolute positioning + camera error budget (not from UR)
        if inner is None:
            if tgt.size is not None and tgt.size_basis == "stated":
                inner = tgt.size
            else:
                self.add(Finding("container_fit", Status.UNKNOWN, f"interior size of '{tgt.id}' is unknown; '{held.id}' needs an opening of at least {(w + margin) * 1000:.0f} mm plus the tool's width",
                                 evidence={"object_footprint_m": round(w, 3), "object_size_basis": held.size_basis}, conditions=[f"interior of '{tgt.id}' >= {(w + margin) * 1000:.0f} mm + tool clearance in both horizontal directions"],
                                 needs=[f"interior dimensions of '{tgt.id}'", "tool width at the contact end"], basis="unknown"), step)
                return
        free = float(min(inner[:2])) - w
        ev = {"object_footprint_m": round(w, 3), "container_inner_min_m": round(float(min(inner[:2])), 3), "free_clearance_m": round(free, 3)}
        if free < margin:
            self.add(Finding("container_fit", Status.FAIL, f"only {free * 1000:.0f} mm free around '{held.id}' inside '{tgt.id}' (need >= {margin * 1000:.0f} mm for the positioning error budget)", evidence=ev, basis="computed"), step)
        else:
            self.add(Finding("container_fit", Status.CONDITIONAL, f"'{held.id}' fits inside '{tgt.id}' with {free * 1000:.0f} mm to spare; the tool's own width must also fit",
                             evidence=ev, conditions=["tool tip width fits in the remaining clearance"], needs=["tool width at the contact end"], basis="computed"), step)

    # ------------------------------------------------------------------------------------ timing
    def check_timing(self) -> None:
        names = ("max_duration_s", "time_limit_s", "deadline_s", "max_time_s", "cycle_time_s", "max_duration", "time_limit")
        limit = as_float(find_value(self.skill.constraints, names, max_depth=3))
        if limit is None:
            limit = as_float(find_value({"steps": [s.params for s in self.skill.steps]}, names, max_depth=3))
        t_min = self.metrics.get("min_arm_time_s (joint-speed lower bound, no acceleration limits)")
        if limit is None or t_min is None:
            return
        ev = {"required_max_duration_s": limit, "hardware_lower_bound_s": t_min}
        if limit < t_min:
            self.add(Finding("timing", Status.FAIL, f"{limit:g} s is shorter than the arm's absolute minimum {t_min:.2f} s (joint speed limit alone)", evidence=ev, sources=["ur5e:limits.joint_speed_max_deg_s"], basis="computed"))
        elif limit < 3 * t_min:
            self.add(Finding("timing", Status.CONDITIONAL, f"{limit:g} s leaves little room over the {t_min:.2f} s lower bound (acceleration limits are not published by UR)", evidence=ev,
                             conditions=["motion near full joint speed with unpublished acceleration limits"], sources=["ur5e:limits.joint_speed_max_deg_s"], basis="computed"))
        else:
            self.add(Finding("timing", Status.PASS, f"{limit:g} s is well above the arm's {t_min:.2f} s lower bound", evidence=ev, sources=["ur5e:limits.joint_speed_max_deg_s"], basis="computed"))

    # ------------------------------------------------------------------------------------ assemble
    def assemble(self, specs: dict[int, ActionSpec | None]) -> FeasibilityReport:
        steps: list[StepReport] = []
        for s in self.skill.steps:
            fs = self.step_findings.get(s.index, [])
            if not fs:
                fs = [Finding("info", Status.INFO, "no hardware requirement beyond the arm being powered", step_id=s.id)]
            spec = specs[s.index]
            desc = str(s.raw.get("description", "")) or (spec.description if spec else "")
            steps.append(StepReport(step_id=s.id, action=s.action, target=s.target, description=desc, status=worst([f.status for f in fs]), findings=fs))
        all_findings = [f for sr in steps for f in sr.findings] + self.global_findings
        overall = worst([f.status for f in all_findings])
        c = self.cell
        has_assumed = any(x != "confirmed" for x in (c.base_status, c.table_status, c.mounting_status))
        if overall == Status.FAIL:
            detail = "infeasible"
        elif overall == Status.UNKNOWN:
            detail = "needs_validation"
        elif overall == Status.CONDITIONAL or has_assumed:
            detail = "feasible_with_conditions"
        else:
            detail = "feasible"
        verdict = Verdict.YES if detail in ("feasible", "feasible_with_conditions") else Verdict.NO
        conditions: list[str] = []
        needs: list[str] = []
        for f in all_findings:
            for c in f.conditions:
                if c not in conditions:
                    conditions.append(c)
            for n in f.needs:
                if n not in needs:
                    needs.append(n)
        if has_assumed:
            conditions.append("the cell assumptions listed below are correct (robot base pose, table height, upright mounting)")
        fails = [f for f in all_findings if f.status == Status.FAIL]
        unknowns = [f for f in all_findings if f.status == Status.UNKNOWN]
        summary = self.summary_text(detail, fails, unknowns, steps)
        return FeasibilityReport(
            skill_id=self.skill.id, skill_name=self.skill.name, robot=self.robot.name, tool=self.tool.name, camera=self.cam.name,
            verdict=verdict, physically_possible_if_conditions_hold=not fails, detail=detail, summary=summary, steps=steps,
            global_findings=self.global_findings, conditions=conditions, missing_information=needs, assumptions=list(dict.fromkeys(self.assumptions)),
            metrics=self.metrics, profile_conflicts=self.robot.conflicts, parse_issues=self.skill.issues,
        )

    def summary_text(self, verdict: str, fails: list[Finding], unknowns: list[Finding], steps: list[StepReport]) -> str:
        if verdict == "infeasible":
            lines = [f"This skill CANNOT be executed with the listed hardware: {len(fails)} hard blocker(s)."]
            for f in fails[:6]:
                lines.append(f"  - [{f.step_id or 'skill'}] {f.check}: {f.message}")
            reach = [f for sr in steps for f in sr.findings if f.check == "reach_and_path"]
            if reach and not any(f.status == Status.FAIL for f in reach):
                lines.append(f"The arm itself is not the blocker: reach, joint limits and path continuity are fine for all {len(reach)} motion step(s).")
            elif reach:
                lines.append("Reach / joint-limit / path problems also exist (see the steps below).")
            return "\n".join(lines)
        if verdict == "needs_validation":
            return f"No hard blocker found, but {len(unknowns)} item(s) cannot be decided without more information (see 'Information needed'), so the answer is NO until they are supplied."
        if verdict == "feasible_with_conditions":
            return "No hard blocker found. The hardware can physically execute the skill provided the listed conditions hold."
        return "The hardware can physically execute the skill; every requirement is met with the documented numbers."


def check_skill(skill: Skill, profiles: Profiles | None = None) -> FeasibilityReport:
    return Checker(skill, profiles or load_profiles()).run()
