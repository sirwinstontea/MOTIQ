// What to do when a Grok Voice turn ends. grok-voice-latest reasons by default and often
// writes the answer as text, or says "on it", without calling a tool or sending audio.
import { isNo, isYes, normalizeUtterance } from '../lib/armApproval';

// A request that needs a camera or the arm. Greetings and "who are you" do not match.
const ACT =
  /\b(look|camera|see|watch|check|scan|find|search|pick|place|grab|put|move|home|gripper|open|close|skill|guarda|guardare|vedi|vedere|controlla|cerca|prendi|prendere|posa|posare|muovi|muovere|braccio|pinza|apri|chiudi)\b/i;

export function needsToolNudge(user: string, assistant: string, already: boolean): boolean {
  if (already) return false;
  const u = normalizeUtterance(user);
  if (!u || isNo(u)) return false;
  // "yes" with no tool means the plan was approved and nothing ran
  if (isYes(u)) return true;
  if (ACT.test(user)) return true;
  // "on it" / "I'll do it" after a real request, with no tool, is a stalled turn
  return ACT.test(assistant) && /\b(on it|i['’]?ll|i will|let me|lo faccio|ci penso)\b/i.test(assistant);
}

export function shouldSpeakText(text: string, hadAudio: boolean, willNudge: boolean): boolean {
  return !hadAudio && !willNudge && text.trim().length > 0;
}

// response.done sometimes carries the words even when no delta event arrived
export function textFromDone(ev: { response?: { output?: unknown } }): string {
  const out = ev.response?.output;
  if (!Array.isArray(out)) return '';
  const bits: string[] = [];
  for (const item of out) {
    const content = (item as { content?: unknown }).content;
    if (!Array.isArray(content)) continue;
    for (const part of content) {
      const p = part as { transcript?: unknown; text?: unknown };
      if (typeof p.transcript === 'string' && p.transcript) bits.push(p.transcript);
      else if (typeof p.text === 'string' && p.text) bits.push(p.text);
    }
  }
  return bits.join('').trim();
}
