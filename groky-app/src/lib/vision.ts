import env from './config';

const SYSTEM =
  'You are the eyes of a robot crew (a robot dog and a robot arm). Describe what matters for the robots: objects, colours, ' +
  'rough positions (left/right/near/far), people, obstacles. Max 3 short sentences. If asked a yes/no question, start with yes or no.';

function outputText(j: any): string {
  if (typeof j.output_text === 'string') return j.output_text;
  for (const o of j.output ?? []) {
    for (const c of o.content ?? []) if (c.type === 'output_text' && c.text) return c.text;
  }
  return j.choices?.[0]?.message?.content ?? '';
}

// Ask Grok about a jpeg. Goes through our backend if VISION_URL is set, otherwise straight to xAI (dev).
export async function look(jpegB64: string, question = 'What do you see?'): Promise<string> {
  const body = {
    model: env.visionModel,
    store: false,
    input: [
      { role: 'system', content: SYSTEM },
      {
        role: 'user',
        content: [
          { type: 'input_image', image_url: `data:image/jpeg;base64,${jpegB64}`, detail: 'high' },
          { type: 'input_text', text: question },
        ],
      },
    ],
  };

  const url = env.visionUrl || 'https://api.x.ai/v1/responses';
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (!env.visionUrl) {
    if (!env.xaiKey) throw new Error('vision needs EXPO_PUBLIC_XAI_API_KEY or EXPO_PUBLIC_VISION_URL');
    headers.Authorization = `Bearer ${env.xaiKey}`;
  }

  const r = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error?.message ?? j.error ?? `vision ${r.status}`);
  return outputText(j).trim() || 'I could not make anything out.';
}
