import env from './config';
import { xaiFetch } from './xai';

const SYSTEM =
  'You are the eyes of a UR5e robot arm working on a table. Describe what matters for the arm: objects, colours, ' +
  'rough positions (left/right/near/far), people, obstacles. Max 3 short sentences. If asked a yes/no question, start with yes or no.';

function outputText(j: any): string {
  if (typeof j.output_text === 'string') return j.output_text;
  for (const o of j.output ?? []) {
    for (const c of o.content ?? []) if (c.type === 'output_text' && c.text) return c.text;
  }
  return j.choices?.[0]?.message?.content ?? '';
}

// Ask Grok about a jpeg. Goes through our backend if VISION_URL is set, otherwise straight to xAI (dev).
export async function look(
  jpegB64: string,
  question = 'What do you see?',
  detail: 'low' | 'high' = 'high',
): Promise<string> {
  const body = {
    model: env.visionModel,
    store: false,
    input: [
      { role: 'system', content: SYSTEM },
      {
        role: 'user',
        content: [
          { type: 'input_image', image_url: `data:image/jpeg;base64,${jpegB64}`, detail },
          { type: 'input_text', text: question },
        ],
      },
    ],
  };

  const r = env.visionUrl
    ? await fetch(env.visionUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    : await xaiFetch('/responses', body);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error?.message ?? j.error ?? `vision ${r.status}`);
  return outputText(j).trim() || 'I could not make anything out.';
}
