import { Platform } from 'react-native';
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
  if (env.xaiKey) {
    // Browsers can't send an Authorization header on a WebSocket, so on web trade the key for a short-lived client
    // secret (sent as a subprotocol). Dev only: the key is still inside the page.
    if (Platform.OS === 'web') {
      const r = await fetch('https://api.x.ai/v1/realtime/client_secrets', {
        method: 'POST',
        headers: { Authorization: `Bearer ${env.xaiKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ expires_after: { seconds: 300 } }),
      });
      if (!r.ok) throw new Error(`xAI rejected the API key (${r.status})`);
      const j = await r.json();
      if (!j.value) throw new Error('xAI returned no client secret');
      return j.value;
    }
    return env.xaiKey;
  }
  throw new Error('No EXPO_PUBLIC_TOKEN_URL or EXPO_PUBLIC_XAI_API_KEY in .env');
}
