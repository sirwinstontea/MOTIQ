export type Status = 'off' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'acting';

export type Line =
  | { id: string; kind: 'user' | 'grok'; text: string }
  | { id: string; kind: 'tool'; name: string; args: string; ok?: boolean; ms?: number; at?: number }
  | { id: string; kind: 'error'; text: string };

// what the agent is doing, for the activity panel
export type Trace = {
  id: string;
  at: number;
  kind: 'hear' | 'think' | 'plan' | 'tool' | 'vision' | 'speak' | 'error';
  title: string;
  detail?: string;
  ok?: boolean;
  ms?: number;
  image?: string; // small jpeg, base64
};

export type Looker = (question: string) => Promise<{ ok: boolean; source: string; answer: string; image?: string }>;
