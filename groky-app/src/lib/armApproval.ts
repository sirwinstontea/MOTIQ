// Motion approval. Motion (Grok voice or typed chat) may plan tasks and skills, but before the arm moves —
// simulator or real — it must say what it will do (propose_plan) and get a spoken or typed yes ("yes", "go ahead",
// "sì", "oui", "はい", …). No separate on-screen Approve card: voice/chat yes/no is enough. Skill files still use
// Skills-tab approval for real-robot runs. The voice session refuses arm tools and run_skill until then (session.ts).
// A new request, a "no", a stop, or 5 minutes ends an approval. Grok can't approve its own plan.
// Phrases cover every language Grok Voice lists as supported (docs.x.ai Speech to Speech), so a short yes/no in
// the visitor's language still unlocks or cancels the plan after auto language detection.
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

// Longest phrases first so "go ahead" / "va bene" / "d'accord" win over a lone first word.
// Covers Grok Voice supported languages: en, it, fr, de, es, pt, ja, zh, ko, hi, ar, ru, tr, vi, id, bn, …
const YES =
  /^(go ahead|do it|sounds good|let's go|ok vai|va bene|d'accord|de acuerdo|pode ser|all right|alright|yes please|yeah|yep|yup|yes|sure|okay|ok|go|approve|approved|confirm|confirmed|proceed|si|sì|sí|sim|vai|procedi|certo|fallo|oui|ouais|vas-y|ja|genau|klar|machen|los|vale|adelante|claro|dale|por favor|evet|tamam|vâng|vang|được|duoc|có|co|ya|iya|baik|oké|oke|да|хорошо|ладно|хорошо да|نعم|أيوة|ايوة|أجل|اجل|حسنا|حسناً|हां|हाँ|जी|जी हाँ|হ্যাঁ|ঠিক আছে|はい|ええ|うん|よし|お願い|いいよ|是|好|好的|可以|行|行吧|中|네|예|좋아|응|그래)(?=\s|$)/u;

const NO =
  /^(do not|don't|hold on|pas maintenant|nope|cancel|wait|stop|no|annulla|aspetta|fermo|ferma|non|nono|nah|nein|stopp|warte|abbrechen|cancelar|espera|para|alto|não|nao|cancela|hayır|hayir|dur|không|khong|đừng|dung|tidak|jangan|batal|нет|не|стоп|отмена|لا|لأ|توقف|ألغ|नहीं|नही|रुक|না|থাম|いいえ|やめて|だめ|ダメ|キャンセル|待って|不|不要|别|取消|停下|아니요|아니|취소|멈춰|그만)(?=\s|$)/u;

/** Normalize a spoken/typed utterance for yes/no matching across scripts. */
export function normalizeUtterance(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    // STT often emits curly quotes; map them to ASCII so "d'accord" / "let's" still match.
    .replace(/[\u2018\u2019\u2032`´]/g, "'")
    // Keep ASCII apostrophes (d'accord, let's, don't). Strip other sentence punctuation.
    .replace(/[.,!?¿¡;:…。！？、，｡«»""]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Short answers only: "yes, but put it in the box" is a change, not an approval. */
export function isShortApproval(text: string): boolean {
  if (!text || text.length > 48) return false;
  const words = text.split(' ').filter(Boolean);
  // Space-delimited languages: up to 6 words. Scripts without spaces are gated by length above.
  return words.length <= 6;
}

export function isYes(text: string): boolean {
  return YES.test(text) && isShortApproval(text);
}

export function isNo(text: string): boolean {
  return NO.test(text);
}

// Every finished user utterance (spoken or typed) goes through here. Returns what it did to the plan.
export function userSaid(raw: string, via: 'voice' | 'typed'): 'approved' | 'rejected' | 'new_request' | null {
  const text = normalizeUtterance(raw);
  if (!text || !plan) return null;
  if (plan.state === 'pending') {
    if (isNo(text)) return reject(), 'rejected';
    if (isYes(text)) return approve(via), 'approved';
    expire(); // they answered with something else (a change, a new task): Motion proposes again
    return 'new_request';
  }
  if (plan.state === 'approved') {
    if (isNo(text)) return reject(), 'rejected';
    if (isYes(text)) return null; // "yes, go on" keeps it
    expire(); // a new request needs a new plan
    return 'new_request';
  }
  return null;
}

/** Reset module state (tests only). */
export function __resetForTests() {
  plan = null;
  emit();
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
