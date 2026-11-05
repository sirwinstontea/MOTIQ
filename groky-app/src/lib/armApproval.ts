// Real-arm approval. Motion (Grok voice or typed chat) has full authorization to plan and run tasks and skills,
// but before the REAL arm moves it must say what it will do (propose_plan) and get a yes from a person:
// spoken or typed ("yes", "go ahead", "sì", ...), or the Approve button on screen. The voice session refuses
// real-arm tools until then (session.ts). A new request from the user, a "no", a stop, or 5 minutes end an approval.
// Grok can't approve its own plan: approval only comes from the user's own words or a tap.
import { useSyncExternalStore } from 'react';

export type Plan = {
  id: string;
  say: string; // what Motion told the user it will do
  steps: string[];
  state: 'pending' | 'approved' | 'rejected' | 'expired';
  at: number;
  approvedAt?: number;
  via?: 'voice' | 'typed' | 'tap';
};

const VALID_MS = 5 * 60 * 1000;
let plan: Plan | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const set = (p: Plan | null) => {
  plan = p;
  emit();
};

export const currentPlan = () => plan;

export function propose(say: string, steps: string[]): Plan {
  const p: Plan = { id: `plan_${Date.now().toString(36)}`, say, steps, state: 'pending', at: Date.now() };
  set(p);
  return p;
}

export function approve(via: Plan['via']) {
  if (plan?.state === 'pending') set({ ...plan, state: 'approved', approvedAt: Date.now(), via });
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

// Every finished user utterance (spoken or typed) goes through here. Returns what it did to the plan.
export function userSaid(raw: string, via: 'voice' | 'typed'): 'approved' | 'rejected' | 'new_request' | null {
  const text = raw.trim().toLowerCase().replace(/[.,!?]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!text || !plan) return null;
  if (plan.state === 'pending') {
    if (NO.test(text)) return reject(), 'rejected';
    if (YES.test(text) && text.split(' ').length <= 6) return approve(via), 'approved';
    expire(); // they answered with something else (a change, a new task): Motion proposes again
    return 'new_request';
  }
  if (plan.state === 'approved') {
    if (NO.test(text)) return reject(), 'rejected';
    if (YES.test(text) && text.split(' ').length <= 6) return null; // "yes, go on" keeps it
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
