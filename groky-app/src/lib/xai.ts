import env from './config';

/* One way to call xAI from the app.
   - Installed MOTIQ: EXPO_PUBLIC_XAI_PROXY points at the launcher's local proxy, which adds the xAI key on the PC.
     The app only sends the MOTIQ bridge key, so the xAI key is never inside the app or its JavaScript bundle.
   - Developer setup: EXPO_PUBLIC_XAI_API_KEY in groky-app/.env, called directly (the key is then in the page). */
export const hasXai = (): boolean => !!(env.xaiProxy || env.xaiKey);

export async function xaiFetch(path: string, body: unknown): Promise<Response> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  let url: string;
  if (env.xaiProxy) {
    url = `${env.xaiProxy}/v1${path}`;
    headers['X-Motiq-Key'] = env.bridgeKey;
  } else if (env.xaiKey) {
    url = `https://api.x.ai/v1${path}`;
    headers.Authorization = `Bearer ${env.xaiKey}`;
  } else {
    throw new Error('No xAI key. Add one in MOTIQ Settings.');
  }
  return fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
}
