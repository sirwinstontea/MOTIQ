// Full access: Motion (voice or chat) may run skills and multi-step tasks on the REAL arm without approving each skill
// in the Skills tab first. ON by default; before the real arm moves Motion still tells the plan and waits for a yes
// (lib/armApproval.ts). Turned off by voice ("safe mode", "full access off") or in Settings.
// The bridge's own limits still apply (small slow steps, RUNNING + NORMAL safety, STOP always works).
import { useSyncExternalStore } from 'react';

let on = true;
const listeners = new Set<() => void>();

export const fullAccess = () => on;

export function setFullAccess(next: boolean) {
  if (next === on) return;
  on = next;
  listeners.forEach((l) => l());
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export const useFullAccess = () => useSyncExternalStore(subscribe, fullAccess, fullAccess);
