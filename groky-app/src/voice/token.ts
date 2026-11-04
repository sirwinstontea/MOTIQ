import env from '../lib/config';

export async function getToken(): Promise<string> {
  if (env.tokenUrl) {
    const r = await fetch(env.tokenUrl, { method: 'POST' });
    if (!r.ok) throw new Error(`token server ${r.status}`);
    const j = await r.json();
    // response shape isn't documented, accept the usual suspects
    const t = j.value ?? j.client_secret?.value ?? j.token ?? j.secret;
    if (!t) throw new Error('token server returned no token');
    return t;
  }
  if (env.xaiKey) return env.xaiKey;
  throw new Error('No EXPO_PUBLIC_TOKEN_URL or EXPO_PUBLIC_XAI_API_KEY in .env');
}
