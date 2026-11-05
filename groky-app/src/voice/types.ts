export type Status = 'off' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'acting';

export type Line =
  | { id: string; kind: 'user' | 'grok'; text: string }
  | { id: string; kind: 'tool'; name: string; args: string; ok?: boolean; ms?: number; at?: number; speed?: number }
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
  speed?: number; // arm speed the task ran at, % of model max
  image?: string; // small jpeg, base64
  active?: boolean; // still going (Show Motion recording / learning): lights its stage even while Motion is idle
};

export type Looker = (question: string) => Promise<{ ok: boolean; source: string; answer: string; image?: string }>;

export type SkillRunner = {
  list: () => { id: string; name: string; description: string }[];
  // target 'robot' = the physical arm while the PC stack runs SIM=1 (the user picked "real arm" for the plan)
  run: (id: string, target?: 'robot') => Promise<{ ok: boolean; [k: string]: unknown }>;
  // change an existing saved skill in place (Grok rewrites it), never creates a new one
  edit: (id: string, change: string) => Promise<{ ok: boolean; [k: string]: unknown }>;
};
