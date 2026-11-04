// Run a skill against the arm model without touching anything: feasibility check + data for the preview.
import { applyArm, ArmState, evaluate, initialArm, Keyframe, resolveParams, World, worldFromEnv } from '../robot/arm';
import { armOpts, deviceOf, isLookStep, primitivesOf, primLabel } from './index';
import type { SkillDoc, SkillStep } from './schema';

export type Beat = {
  step: number;
  prim: number | null; // null = the step itself (DETECT / VERIFY)
  label: string;
  note: string;
  warn: boolean;
  error?: string;
  scan: boolean;
  seconds: number;
  frames: Keyframe[]; // arm only
};

export type DryRun = { beats: Beat[]; error?: string; warnings: string[]; start: ArmState; world: World };

// what a VERIFY step checks, from its parameters and the skill's object constraints
export function verifyCheck(doc: SkillDoc, step: SkillStep) {
  const object = step.target;
  if (!object) return undefined;
  const cons = doc.constraints?.object_constraints ?? [];
  const container = cons.find((c) => c.type === 'containment' && c.object === object)?.container as string | undefined;
  const p = step.parameters ?? {};
  return {
    object,
    container,
    containment: p.check_containment !== false && !!container,
    orientation:
      p.check_orientation === true ||
      cons.some((c) => c.type === 'orientation' && c.object === object && c.required_value === 'upright'),
  };
}

export function dryRun(doc: SkillDoc, start: ArmState = initialArm()): DryRun {
  const device = deviceOf(doc);
  const prims = primitivesOf(doc);
  const opts = armOpts(doc);
  const grasp = doc.constraints?.execution_constraints?.require_grasp_validation !== false;
  let state = start;
  let world = worldFromEnv(doc.environment?.objects ?? []);
  const world0 = world;
  const beats: Beat[] = [];
  const warnings: string[] = [];
  let error: string | undefined;
  const hold = (dt: number): Keyframe[] => [{ tcp: state.tcp, grip: state.gripper.closed ? 1 : 0, world, dt }];

  outer: for (const [i, step] of doc.steps.entries()) {
    if (isLookStep(step)) {
      const name = world[step.target ?? '']?.name ?? step.target ?? 'the scene';
      let note = step.action === 'DETECT' ? `Looks for the ${name}` : `Camera check on the ${name}`;
      let warn = false;
      if (step.action === 'VERIFY' && device === 'arm') {
        const check = verifyCheck(doc, step);
        if (check) {
          const e = evaluate(world, state, check.object, check.container);
          const bad = (check.containment && !e.inside) || (check.orientation && !e.upright) || e.held;
          note = bad
            ? `Check would likely fail: the ${name} is ${e.where}`
            : `Check should pass: the ${name} is ${e.where}`;
          warn = !!bad;
          if (bad) warnings.push(`Step ${i + 1} VERIFY would likely fail: the ${name} is ${e.where}.`);
        }
      }
      beats.push({
        step: i,
        prim: null,
        label: step.action,
        note,
        warn,
        scan: true,
        seconds: 1.2,
        frames: device === 'arm' ? hold(1.2) : [],
      });
      continue;
    }

    for (const [j, call] of (step.primitive_sequence ?? []).entries()) {
      const label = `${step.action} · ${primLabel(call)}`;
      if (device === 'dog') {
        const p = prims.find((x) => x.id === call.primitive);
        const r = p ? resolveParams(p, call.parameters ?? {}, false) : { error: `unknown primitive ${call.primitive}` };
        const err = 'error' in r ? r.error : undefined;
        beats.push({
          step: i,
          prim: j,
          label,
          note: err ?? p!.description,
          warn: !!err,
          error: err,
          scan: false,
          seconds: 1,
          frames: [],
        });
        if (err) {
          error = `Step ${i + 1}: ${err}`;
          break outer;
        }
        continue;
      }
      const out = applyArm(call.primitive, call.parameters, state, world, opts, prims);
      if (!out.ok) {
        error = `Step ${i + 1} (${step.action}), ${out.error?.startsWith(call.primitive) ? out.error : `${call.primitive}: ${out.error}`}`;
        beats.push({
          step: i,
          prim: j,
          label,
          note: out.error ?? 'fails',
          warn: true,
          error: out.error,
          scan: false,
          seconds: 0.5,
          frames: hold(0.5),
        });
        break outer;
      }
      let warn = false;
      let note = out.note;
      if (call.primitive === 'CLOSE_GRIPPER' && out.result.object_detected === false) {
        warn = true;
        if (grasp) {
          note = 'Gripper closes on nothing: the grasp check would fail here';
          warnings.push(`Step ${i + 1} (${step.action}): the gripper would close on nothing.`);
        }
      }
      state = out.state;
      world = out.world;
      beats.push({ step: i, prim: j, label, note, warn, scan: false, seconds: out.seconds, frames: out.frames });
    }
  }
  return { beats, error, warnings, start, world: world0 };
}
