import env from '../lib/config';
import type { SkillDoc } from './schema';
import { deviceOf } from './index';

const MODEL = process.env.EXPO_PUBLIC_GROK_SKILL_MODEL || env.visionModel;

function outputText(j: any): string {
  if (typeof j.output_text === 'string') return j.output_text;
  for (const o of j.output ?? []) {
    for (const c of o.content ?? []) if (c.type === 'output_text' && c.text) return c.text;
  }
  return j.choices?.[0]?.message?.content ?? '';
}

// Ask Grok for a short name (2-4 words) describing what the skill does. Null if it can't be had.
export async function nameSkill(doc: SkillDoc, said: string): Promise<string | null> {
  if (!env.xaiKey) return null;
  const steps = doc.steps
    .map((s, i) => `${i + 1}. ${s.action}: ${s.description ?? ''}`.trim())
    .slice(0, 12)
    .join('\n');
  const input = [
    {
      role: 'system',
      content:
        'You name robot skills. Reply with ONLY a short title of 2 to 4 words, Title Case, describing the action ' +
        '(for example "Pick Orange Screw", "Dog Greeting Wave"). No quotes, no punctuation, no extra text.',
    },
    { role: 'user', content: `The operator said: "${said}"\nDevice: ${deviceOf(doc)}\nSteps:\n${steps}` },
  ];
  try {
    const r = await fetch('https://api.x.ai/v1/responses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${env.xaiKey}` },
      body: JSON.stringify({ model: MODEL, store: false, input }),
    });
    if (!r.ok) return null;
    const text = outputText(await r.json()).split('\n')[0].replace(/["'.]/g, '').trim();
    return text && text.length <= 40 ? text : null;
  } catch {
    return null;
  }
}
