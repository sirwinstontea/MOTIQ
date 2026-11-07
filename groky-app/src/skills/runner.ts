// Executes a skill step by step: DETECT / VERIFY look through the camera (or the simulator), everything else runs
// its primitive_sequence. Failure policies and the safety gates from the skill file are applied here.
import { armPhysical, armSend, bridgeIsSim, devices, runTool, setArmTargetReal, stopDevice } from '../devices';
import { armState, armWorld, detect, runArm, setWorld, syncArmFromBridge, verify } from '../robot/runtime';
import { worldFromEnv } from '../robot/arm';
import { verifyCheck } from './dryrun';
import { armOpts, CELL_OBJECTS, deviceOf, physicalArmProblems, physicalBlock, touchSimSequence } from './index';
import type { PrimitiveCall, SkillDoc, SkillStep } from './schema';

export type StepResult = { ok: boolean; ms: number; error?: string; note?: string; attempts: number };
export type Token = { cancelled: boolean };
export type Progress = { step: number; prim: number | null };

type Res = { ok: boolean; error?: string; note?: string; refused?: boolean };

const wait = (ms: number, token: Token) =>
  new Promise<void>((resolve) => {
    const end = Date.now() + ms;
    const tick = () => (token.cancelled || Date.now() >= end ? resolve() : setTimeout(tick, 100));
    tick();
  });

// where a run happens: 'sim' = the tablet's own simulator (nothing is sent to the bridge, not even stop),
// 'real' = through the arm bridge. Omitted: the bridge when one is configured (voice runs), else the simulator.
// 'robot' = the PHYSICAL arm while the PC stack runs SIM=1 (bridge X-Arm-Target: real; needs the real-arm approval).
export type RunTarget = 'sim' | 'real' | 'robot';

type Callbacks = { progress: (p: Progress) => void; result: (i: number, r: StepResult) => void };

export async function runSkill(doc: SkillDoc, token: Token, on: Callbacks, target?: RunTarget) {
  if (target !== 'robot') return runSkillOn(doc, token, on, target);
  const problems = physicalArmProblems(doc);
  if (problems.length) return { ok: false, state: 'failed' as const, error: problems[0], results: [] };
  setArmTargetReal(true);
  try {
    return await runSkillOn(doc, token, on, target);
  } finally {
    setArmTargetReal(false);
  }
}

async function runSkillOn(
  doc: SkillDoc,
  token: Token,
  on: { progress: (p: Progress) => void; result: (i: number, r: StepResult) => void },
  target?: RunTarget,
): Promise<{ ok: boolean; state: 'done' | 'failed' | 'stopped'; error?: string; results: StepResult[] }> {
  const device = deviceOf(doc);
  if ((target === 'real' || target === 'robot') && !devices[device].url)
    return { ok: false, state: 'failed', error: 'no arm bridge connected (EXPO_PUBLIC_ARM_URL)', results: [] };
  const real = target === 'sim' ? false : !!devices[device].url;
  // 'robot' = the physical arm: the real-robot approval applies even though the bridge runs SIM=1
  const blocked = physicalBlock(doc, real, real && bridgeIsSim(device) && target !== 'robot');
  if (blocked) return { ok: false, state: 'failed', error: blocked, results: [] };

  const opts = armOpts(doc);
  const ec = doc.constraints?.execution_constraints ?? {};
  const trustFile = doc.safety?.unknown_parameters_policy === 'USE_SKILL_VALUES';
  if (device === 'arm') setWorld(worldFromEnv(doc.environment?.objects ?? []));

  const prim = async (c: PrimitiveCall): Promise<Res & { object_detected?: unknown }> => {
    // physical arm on a SIM=1 stack: home is a controller (simulation) move; locate / touch / push already went back
    if (target === 'robot' && c.primitive === 'HOME')
      return { ok: true, note: 'real arm: home skipped (it runs in the simulation only); the arm stays where it is' };
    if (c.primitive === 'WAIT') {
      await wait(Math.min(10, Number(c.parameters?.seconds ?? 0.5)) * 1000, token);
      return { ok: true };
    }
    const r = await runArm(c.primitive, c.parameters ?? {}, real ? armSend() : null, opts);
    return {
      ok: r.ok,
      error: r.ok ? undefined : String(r.error ?? r.status ?? 'failed'),
      note: typeof r.did === 'string' ? r.did : undefined,
      refused: !!r.refused,
      object_detected: r.object_detected,
    };
  };

  const look = async (target: string): Promise<Res> => {
    // Real arm: the camera is on the arm head, so the bridge moves the arm through its look poses until the camera
    // sees the object. Picks then go to the controller's taught spot, so no measured position is needed here.
    if (device === 'arm' && real) {
      const r = await runTool('arm_find', { object: target });
      return { ok: r.ok, note: typeof r.did === 'string' ? r.did : undefined, error: r.ok ? undefined : String(r.error ?? 'not found'), refused: !!r.refused };
    }
    const d = await detect(target, real);
    if (!d.ok) return { ok: false, error: d.error };
    if (real && !d.measured && !trustFile)
      return {
        ok: false,
        error: `the ${armWorld()[target]?.name ?? target} was seen but its position isn't measured (arm camera not calibrated). Set unknown_parameters_policy to USE_SKILL_VALUES to use the positions in the skill file.`,
      };
    return { ok: true, note: d.note };
  };

  const sequence = async (i: number, step: SkillStep): Promise<Res> => {
    const notes: string[] = [];
    for (const [j, c] of (step.primitive_sequence ?? []).entries()) {
      if (token.cancelled) return { ok: false, error: 'stopped' };
      on.progress({ step: i, prim: j });
      const r = await prim(c);
      if (!r.ok) return { ...r, error: r.error?.startsWith(c.primitive) ? r.error : `${c.primitive}: ${r.error}` };
      if (c.primitive === 'CLOSE_GRIPPER' && ec.require_grasp_validation !== false && r.object_detected === false)
        return { ok: false, error: 'grasp failed: nothing in the gripper' };
      if (r.note) notes.push(r.note);
    }
    return { ok: true, note: notes.at(-1) };
  };

  const once = async (i: number, step: SkillStep): Promise<Res> => {
    on.progress({ step: i, prim: null });
    if (step.action === 'OVERVIEW') {
      // Real arm: the bridge reads the arm camera from where the arm is now; nothing moves.
      if (device === 'arm' && real) {
        const r = await runTool('arm_overview', {});
        return { ok: r.ok, note: typeof r.did === 'string' ? r.did : undefined, error: r.ok ? undefined : String(r.error ?? 'camera unavailable') };
      }
      const seen: string[] = [];
      const missing: string[] = [];
      for (const id of CELL_OBJECTS) ((await detect(id, false)).ok ? seen : missing).push(armWorld()[id]?.name ?? id);
      return seen.length
        ? { ok: true, note: `sees ${seen.join(', ')}${missing.length ? `; not in view: ${missing.join(', ')}` : ''}` }
        : { ok: false, error: 'none of the 4 objects is in view' };
    }
    if (step.action === 'DETECT') return step.target ? look(step.target) : { ok: false, error: 'no target' };
    if (step.action === 'VERIFY' && device === 'arm' && real && step.target && CELL_OBJECTS.includes(step.target)) {
      // look at where the skill put it with the arm camera (destination = the containment constraint, else the last
      // PLACE before this step)
      const dest =
        verifyCheck(doc, step)?.container ??
        doc.steps.slice(0, i).reverse().find((s) => s.action === 'PLACE' && s.target)?.target;
      if (dest) {
        const r = await runTool('arm_check_placed', { object: step.target, destination: dest });
        return { ok: r.ok, note: typeof r.did === 'string' ? r.did : undefined, error: r.ok ? undefined : String(r.error ?? 'not confirmed') };
      }
    }
    if (step.action === 'VERIFY') {
      const conds = step.success_conditions ?? doc.intent?.success_conditions ?? [];
      const v = await verify(conds, real, device === 'arm' ? verifyCheck(doc, step) : undefined);
      return { ok: v.ok, note: v.note, error: v.error };
    }
    // The bridge looks with the arm camera, then runs one checked controller task at that point.
    // The primitive_sequence is what the tablet simulator runs.
    if (device === 'arm' && real && (step.action === 'PICK' || step.action === 'PLACE') && step.target) {
      const r =
        step.action === 'PICK'
          ? await runTool('arm_pick', { object: step.target })
          : await runTool('arm_place', { destination: step.target });
      return {
        ok: r.ok,
        note: typeof r.did === 'string' ? r.did : undefined,
        error: r.ok ? undefined : String(r.error ?? r.status ?? 'failed'),
        refused: !!r.refused,
      };
    }
    // PUSH with the arm bridge connected: the camera finds the object, then one push task goes to that point.
    // The primitive_sequence is only what the app's own simulator shows.
    if (device === 'arm' && real && step.action === 'PUSH' && step.target) {
      const r = await runTool('arm_push', { object: step.target });
      return {
        ok: r.ok,
        note: typeof r.did === 'string' ? r.did : undefined,
        error: r.ok ? undefined : String(r.error ?? r.status ?? 'failed'),
        refused: !!r.refused,
      };
    }
    // Physical arm, TOUCH: the bridge hovers over the target with the camera, comes down until the wrist force sensor
    // feels contact, lifts and goes back. STOP -> stopDevice -> the bridge stops it too.
    if (device === 'arm' && real && step.action === 'TOUCH' && step.target) {
      const r = await runTool('arm_touch', { object: step.target, ...(step.parameters ?? {}) });
      return {
        ok: r.ok,
        note: typeof r.did === 'string' ? r.did : undefined,
        error: r.ok ? undefined : String(r.error ?? r.status ?? 'failed'),
        refused: !!r.refused,
      };
    }
    // Physical arm, VISIT: the vision server (arm camera) guides the arm over the target, sideways at the same height,
    // then the arm goes back to where it started. STOP -> stopDevice -> the bridge stops it too.
    if (device === 'arm' && real && (step.action === 'VISIT' || step.physical_tool === 'ur_visit')) {
      if (!step.target) return { ok: false, error: 'no target to visit' };
      const r = await armPhysical('ur_visit', {
        object: step.target,
        hold_s: Number(step.parameters?.hold_s ?? 2),
        come_back: step.parameters?.come_back !== false,
      });
      // the camera chose where the arm went: the next steps are checked from that real pose
      if (r.ok) syncArmFromBridge(r);
      return {
        ok: r.ok,
        note: typeof r.did === 'string' ? r.did : undefined,
        error: r.ok ? undefined : String(r.error ?? r.status ?? 'failed'),
        refused: !!r.refused,
      };
    }
    // Back to a pose saved on the bridge (save_pose): the bridge turns one joint at a time, with its own height checks.
    // The app's own simulator runs the step's primitive_sequence instead.
    if (device === 'arm' && real && step.physical_tool === 'ur_go_to_pose') {
      const r = await armPhysical('go_to_pose', { name: String(step.parameters?.name ?? '') });
      if (r.ok) syncArmFromBridge(r);
      return {
        ok: r.ok,
        note: typeof r.did === 'string' ? r.did : undefined,
        error: r.ok ? undefined : String(r.error ?? r.status ?? 'failed'),
        refused: !!r.refused,
      };
    }
    // Physical arm, one fixed bridge tool (down + up, no homing). STOP -> stopDevice -> bridge stops it too.
    if (device === 'arm' && real && step.physical_tool === 'ur_down_up') {
      const r = await armPhysical('ur_down_up', { mm: Number(step.parameters?.mm ?? 50) });
      return {
        ok: r.ok,
        note: typeof r.did === 'string' ? r.did : undefined,
        error: r.ok ? undefined : String(r.error ?? r.status ?? 'failed'),
        refused: !!r.refused,
      };
    }
    // re-identify the target right before touching it, if the skill asks for it
    const obj = doc.environment?.objects?.find((o) => o.id === step.target);
    const reid =
      ec.require_object_reidentification !== false &&
      ((step.action === 'PICK' && obj?.detection?.reidentify_before_pick) ||
        (step.action === 'PLACE' && obj?.detection?.reidentify_before_place));
    if (reid && step.target) {
      const d = await look(step.target);
      if (!d.ok) return { ok: false, error: `re-identify ${step.target}: ${d.error}` };
    }
    if (step.action === 'TOUCH' && !step.primitive_sequence?.length && step.target)
      return sequence(i, { ...step, primitive_sequence: touchSimSequence(step.target) });
    if (step.action === 'VISIT' && !step.primitive_sequence?.length && step.target)
      return sequence(i, { ...step, primitive_sequence: [
        { primitive: 'MOVE_ABOVE', parameters: { target: step.target, height_m: 0.15 } },
        { primitive: 'WAIT', parameters: { seconds: Number(step.parameters?.hold_s ?? 2) } },
      ] });
    return sequence(i, step);
  };

  const results: StepResult[] = [];
  let error: string | undefined;
  for (const [i, step] of doc.steps.entries()) {
    if (token.cancelled) break;
    const t0 = Date.now();
    let attempts = 1;
    let r = await once(i, step);
    const policy = step.failure_policy ?? 'REPORT_FAILURE';

    if (!r.ok && !token.cancelled && !r.refused) {
      if (policy === 'STOP_AND_REOBSERVE' && isLook(step)) {
        await wait(1500, token);
        attempts++;
        r = await once(i, step);
      } else if (policy === 'RETRY_WITH_REOBSERVATION') {
        attempts++;
        if (device === 'arm') {
          // let go of nothing, lift clear, look again, then try the whole step once more
          const s = armState();
          if (s.gripper.closed && !s.gripper.holding) await prim({ primitive: 'OPEN_GRIPPER' });
          if (step.target) await look(step.target);
        }
        if (!token.cancelled) r = await once(i, step);
      }
    }
    if (token.cancelled) break;

    const res: StepResult = { ok: r.ok, ms: Date.now() - t0, attempts, note: r.note };
    if (!r.ok) {
      res.error = r.error ?? 'failed';
      if (policy === 'STOP_AND_REPLAN') res.error += ' (needs replanning)';
    }
    results.push(res);
    on.result(i, res);
    if (!r.ok) {
      error = `step ${i + 1} (${step.action}${step.target ? ` ${step.target}` : ''}): ${res.error}`;
      break;
    }
  }

  if (token.cancelled) {
    if (real) await stopDevice(device);
    return { ok: false, state: 'stopped', results };
  }
  // Any failed skill returns the arm to home. A controller fault already tried; this covers a step that
  // stopped before a task (the camera never saw the object) and retries home if the first attempt failed.
  if (error && real && device === 'arm') await prim({ primitive: 'HOME' });
  return { ok: !error, state: error ? 'failed' : 'done', error, results };
}

const isLook = (s: SkillStep) => s.action === 'DETECT' || s.action === 'VERIFY' || s.action === 'OVERVIEW';
