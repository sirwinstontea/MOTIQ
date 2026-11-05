// Real-arm approval. Motion (Grok voice or typed chat) has full authorization to plan and run tasks and skills,
// but before the REAL arm moves it must say what it will do (propose_plan) and get a yes from a person:
// spoken or typed ("yes", "go ahead", "sì", ...), or the Approve button on screen. The voice session refuses
// real-arm tools until then (session.ts). A new request from the user, a "no", a stop, or 5 minutes end an approval.
// Grok can't approve its own plan: approval only comes from the user's own words or a tap.
// When the PC stack runs SIM=1 and also holds the physical arm, the approval also says WHERE to run it:
// 'sim' (URSim + RoboDK) or 'real' (the physical UR5e). A bare "yes" is then not enough: the user picks one.
import { useSyncExternalStore } from 'react';

export type Plan = {
  id: string;
  say: string; // what Motion told the user it will do
  steps: string[];
  state: 'pending' | 'approved' | 'rejected' | 'expired';
  at: number;
  approvedAt?: number;
  via?: 'voice' | 'typed' | 'tap';
  // where the approved plan runs. Fixed when there is nothing to choose; undefined until chosen when `choice`.
  target?: Target;
  choice: boolean; // the user must pick simulation or real arm
};

export type Target = 'sim' | 'real';

const VALID_MS = 5 * 60 * 1000;
let plan: Plan | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const set = (p: Plan | null) => {
  plan = p;
  emit();
};

export const currentPlan = () => plan;

// choice=true: ask "simulation or real arm?"; otherwise `target` is the only place it can run
export function propose(say: string, steps: string[], opts: { choice: boolean; target?: Target }): Plan {
  const p: Plan = {
    id: `plan_${Date.now().toString(36)}`,
    say,
    steps,
    state: 'pending',
    at: Date.now(),
    choice: opts.choice,
    target: opts.choice ? undefined : opts.target,
  };
  set(p);
  return p;
}

// a plan that needs a choice is only approved together with its target
export function approve(via: Plan['via'], target?: Target): boolean {
  if (plan?.state !== 'pending') return false;
  const t = target ?? plan.target;
  if (plan.choice && !t) return false;
  set({ ...plan, state: 'approved', approvedAt: Date.now(), via, target: t });
  return true;
}

// where the currently approved plan runs (null = nothing approved)
export function approvedTarget(): Target | null {
  return approvedNow() ? (plan?.target ?? null) : null;
}

export function reject() {
  if (plan && (plan.state === 'pending' || plan.state === 'approved')) set({ ...plan, state: 'rejected' });
}

export function expire() {
  if (plan && (plan.state === 'pending' || plan.state === 'approved')) set({ ...plan, state: 'expired' });
}

export function approvedNow(): boolean {
  if (plan?.state !== 'approved') return false;
  if (Date.now() - (plan.approvedAt ?? 0) > VALID_MS) {
    expire();
    return false;
  }
  return true;
}

// English + Italian. Only a short answer counts as a yes: "yes, but put it in the box" is a change, not an approval.
const YES =
  /^(yes|yeah|yep|yup|sure|ok|okay|go|go ahead|do it|approve|approved|confirm|confirmed|proceed|sounds good|let's go|si|sì|vai|procedi|va bene|certo|fallo|ok vai)(?=\s|$)/;
const NO = /^(no|nope|cancel|don't|do not|wait|stop|hold on|annulla|aspetta|fermo|ferma|non)(?=\s|$)/;
// "in simulation", "RoboDK", "on the real arm", "for real", "simulazione", "braccio reale"
const SIM = /\b(sim|simulation|simulated|simulator|robodk|ursim|virtual|simulazione|simulato|simulata|simulatore)\b/;
const REAL = /\b(real|physical|reale|vero|fisico)\b/;
const targetIn = (text: string): Target | 'both' | null => {
  const s = SIM.test(text);
  const r = REAL.test(text);
  return s && r ? 'both' : s ? 'sim' : r ? 'real' : null;
};

// Every finished user utterance (spoken or typed) goes through here. Returns what it did to the plan.
// 'need_target' = they said yes but not where (simulation or real arm): the plan stays pending.
export function userSaid(
  raw: string,
  via: 'voice' | 'typed',
): 'approved' | 'rejected' | 'new_request' | 'need_target' | null {
  const text = raw.trim().toLowerCase().replace(/[.,!?]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!text || !plan) return null;
  const short = text.split(' ').length <= 6;
  const where = short ? targetIn(text) : null;
  if (plan.state === 'pending') {
    if (NO.test(text)) return reject(), 'rejected';
    if (where === 'both') return 'need_target';
    // "simulation", "on the real arm", "yes, in RoboDK": an answer with a place approves it there
    if (where && (plan.choice || where === plan.target)) return approve(via, where), 'approved';
    if (YES.test(text) && short) return approve(via) ? 'approved' : 'need_target';
    expire(); // they answered with something else (a change, a new task): Motion proposes again
    return 'new_request';
  }
  if (plan.state === 'approved') {
    if (NO.test(text)) return reject(), 'rejected';
    // switching place after approving ("actually, in simulation") moves the approval there
    if (where && where !== 'both' && plan.choice && where !== plan.target) {
      set({ ...plan, target: where, approvedAt: Date.now(), via });
      return 'approved';
    }
    if ((YES.test(text) || where) && short) return null; // "yes, go on" keeps it
    expire(); // a new request needs a new plan
    return 'new_request';
  }
  return null;
}

// a pending plan can be answered a moment after Grok already acted on a spoken "yes" (the transcript comes in
// later than the tool call): wait briefly for it
export async function waitForAnswer(ms = 3000): Promise<boolean> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (approvedNow()) return true;
    if (plan?.state !== 'pending') return false;
    await new Promise((r) => setTimeout(r, 100));
  }
  return approvedNow();
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
export const usePlan = () => useSyncExternalStore(subscribe, currentPlan, currentPlan);
