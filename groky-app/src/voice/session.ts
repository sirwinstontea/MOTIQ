import { Platform } from 'react-native';
import env, { SAMPLE_RATE } from '../lib/config';
import { bridgeIsSim, bridgeRealArm, devices, grokTools, runTool } from '../devices';
import {
  approvedNow,
  approvedTarget,
  currentPlan,
  needsArmApproval as needsApproval,
  propose,
  reject,
  userSaid,
  waitForAnswer,
} from '../lib/armApproval';
import type { Target } from '../lib/armApproval';
import { fromBase64, toBase64 } from './pcm';
import { Player } from './player';
import { editSkillTool, FULL_ACCESS_NOTE, fullAccessTool, lookTool, PROMPT, proposePlanTool, skillTool } from './prompt';
import { fullAccess, setFullAccess } from '../lib/fullAccess';
import { getToken } from './token';
import { inputTranscription } from './transcription';
import { isRobotTool } from '../skills/fromVoice';
import type { VoiceCommand } from '../skills/fromVoice';
import type { Line, Looker, SkillRunner, Status, Trace } from './types';

type Events = {
  status: (s: Status) => void;
  line: (l: Line) => void;
  trace: (t: Trace) => void;
};

const pretty = (name: string) => name.replace(/_/g, ' ');

// Arm motion, simulation fault reset + run_skill: locked until the user approves Motion's plan,
// including in the simulator — never auto-run after Show Motion / a voice ask.
// arm_touch_teach_save only records an offset; it does not move the arm.

// where an approved arm plan can run. SIM=1 bridge: URSim + RoboDK, and the physical arm too when /health says it
// is reachable (then the user picks). SIM=0 bridge: only the real arm.
function armPlaces(): { choice: boolean; target: Target; realMissing?: boolean } {
  if (!bridgeIsSim('arm')) return { choice: false, target: 'real' };
  const real = bridgeRealArm('arm');
  return real?.reachable ? { choice: true, target: 'sim' } : { choice: false, target: 'sim', realMissing: true };
}
let seq = 0;
const uid = () => `${Date.now().toString(36)}${seq++}`;

export class VoiceSession {
  private ws: WebSocket | null = null;
  private player = new Player();
  private calls: Promise<{ call_id: string; output: string }>[] = [];
  private grokLine: Line | null = null;
  private userLineId: string | null = null;
  private heard: string | null = null;
  private queue: { text: string; record: boolean }[] = [];
  private _status: Status = 'off';
  private speakingTrace: string | null = null;
  private turnStart = 0;
  private looker: Looker | null = null;
  private skills: SkillRunner | null = null;
  private onAsked: ((text: string, via: 'voice' | 'typed') => void) | null = null;
  // the robot actions that ran for the latest spoken request, handed to the app so it can save them as a skill
  private cmd: VoiceCommand | null = null;
  private onCommand: ((cmd: VoiceCommand) => void) | null = null;

  constructor(private on: Events) {
    this.player.onDone = () => {
      if (this._status === 'speaking') this.set('listening');
    };
    // audio starts while the reply is still streaming in; mic goes quiet from here so it can't hear itself
    this.player.onStart = () => {
      if (this._status === 'thinking' || this._status === 'listening') this.set('speaking');
    };
  }

  setLooker(fn: Looker | null) {
    this.looker = fn;
  }

  setSkills(runner: SkillRunner | null) {
    this.skills = runner;
  }

  setCommandHandler(fn: ((cmd: VoiceCommand) => void) | null) {
    this.onCommand = fn;
  }

  // a robot action or skill that Grok ran failed: the app shows it as an error notification
  private onToolError: ((what: string, error: string) => void) | null = null;
  setToolErrorHandler(fn: ((what: string, error: string) => void) | null) {
    this.onToolError = fn;
  }

  // steps that happen outside the conversation (Show Motion recording, learning) shown in "What Motion is doing"
  progress(t: Omit<Trace, 'id' | 'at'> & { id?: string }) {
    this.trace(t);
  }

  // Show Motion: the user demonstrated a task on camera and commented it. Motion answers out loud with what it
  // understood, proposes the plan, and only runs after an explicit yes (simulator or real arm).
  teach(d: { summary: string; skillId: string; skillName: string; transcript?: string }) {
    const said = d.transcript?.trim() ? `\nWhat they said while showing it: "${d.transcript.trim()}"` : '';
    this.sendText(
      `[I just showed you a task through the camera, a short video where I explained it out loud.${said}\n` +
        `The video planner understood:\n${d.summary.split('\nWrite a skill')[0]}\n` +
        `It is saved as the skill "${d.skillName}" (id ${d.skillId}). Answer me out loud in one or two short ` +
        `sentences with what you understood I want and exactly what the arm will do, step by step in plain words. ` +
        `Then call propose_plan for running skill "${d.skillId}" and ask me out loud to approve it. Do NOT call ` +
        `run_skill (or any arm tool) until I say yes (or no to cancel) — even if the arm is only simulated. No ` +
        `separate on-screen approval card: my spoken yes/no is enough. After I say yes, run it with run_skill ` +
        `(skill "${d.skillId}"). If it can't run (for example the skill still needs approval in the Skills tab), ` +
        `tell me why.]`,
      false,
    );
  }

  // skills changed while connected: let Grok know about the new list
  refreshTools() {
    this.send({ type: 'session.update', session: { tools: this.tools() } });
  }

  private tools() {
    const list = this.skills?.list() ?? [];
    return [
      ...grokTools(),
      lookTool,
      fullAccessTool,
      proposePlanTool,
      ...this.visionTools,
      ...(list.length ? [skillTool(list), editSkillTool(list)] : []),
    ];
  }

  // computer-vision server tools (../computer-vision/API_APP.md), prefixed vision_ so they can't clash with the
  // device tools. Empty if the server isn't configured or doesn't answer: the app works as before.
  private visionTools: any[] = [];

  private async loadVisionTools() {
    if (!env.visionServer) return;
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 2500);
    try {
      const tools = await (await fetch(`${env.visionServer}/voice/tools`, { signal: ctl.signal })).json();
      this.visionTools = (Array.isArray(tools) ? tools : []).map((x: any) => ({
        type: 'function',
        ...x,
        name: `vision_${x.name}`,
      }));
    } catch {
      this.visionTools = [];
    } finally {
      clearTimeout(t);
    }
  }

  private async runVision(name: string, args: Record<string, unknown>) {
    try {
      const r = await fetch(`${env.visionServer}/voice/dispatch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, arguments: args }),
      });
      const j = await r.json().catch(() => ({}));
      return { ok: r.ok && j.ok !== false, ...j } as Record<string, unknown> & { ok: boolean };
    } catch (e: any) {
      return { ok: false, error: `vision server unreachable: ${e?.message ?? e}` };
    }
  }

  setAskedHandler(fn: ((text: string, via: 'voice' | 'typed') => void) | null) {
    this.onAsked = fn;
  }

  get status() {
    return this._status;
  }

  // mic only goes up while we're listening - phone speaker would otherwise trigger VAD on itself
  get wantsAudio() {
    return this._status === 'listening' || this._status === 'thinking';
  }

  // connects without touching the mic; useVoice starts the mic separately so typed chat works on its own
  async start() {
    if (this.ws || this._status !== 'off') return; // already connected or connecting
    this.set('connecting');
    let token: string;
    try {
      [token] = await Promise.all([getToken(), this.loadVisionTools()]);
    } catch (e: any) {
      this.error(e.message);
      this.queue = [];
      this.set('off');
      return;
    }

    const url = `wss://api.x.ai/v1/realtime?model=${encodeURIComponent(env.voiceModel)}`;
    // RN's WebSocket accepts headers, unlike the browser one
    // browsers can't set headers: the client secret goes in the subprotocol list instead
    const ws = (
      Platform.OS === 'web'
        ? new WebSocket(url, [`xai-client-secret.${token}`])
        : new (WebSocket as any)(url, null, { headers: { Authorization: `Bearer ${token}` } })
    ) as WebSocket;
    this.ws = ws;

    ws.onopen = () => {
      this.send({
        type: 'session.update',
        session: {
          instructions: PROMPT,
          voice: env.voice,
          turn_detection: { type: 'server_vad' },
          audio: {
            // grok-transcribe is required for hearing captions + spoken yes/no approval; no language_hint
            // keeps auto-detect across all Grok Voice languages (override via EXPO_PUBLIC_GROK_LANGUAGE_HINT).
            input: {
              format: { type: 'audio/pcm', rate: SAMPLE_RATE },
              transcription: inputTranscription(env.languageHint),
            },
            output: { format: { type: 'audio/pcm', rate: SAMPLE_RATE } },
          },
          tools: this.tools(),
        },
      });
      this.set('listening');
      this.trace({
        kind: 'think',
        title: 'Connected to Grok Voice',
        detail: env.languageHint
          ? `${env.voiceModel} · hear ${env.languageHint}`
          : `${env.voiceModel} · hear auto (all languages)`,
      });
      const q = this.queue;
      this.queue = [];
      q.forEach((m) => this.sendText(m.text, m.record));
    };
    ws.onmessage = (m) => this.handle(JSON.parse(m.data as string));
    ws.onerror = () => this.error('connection error');
    ws.onclose = (e) => {
      if (this.ws === ws) {
        if (e.code !== 1000) this.error(`closed (${e.code}) ${e.reason ?? ''}`.trim());
        this.ws = null;
        this.player.stop();
        this.set('off');
      }
    };
  }

  stop() {
    const ws = this.ws;
    this.ws = null;
    ws?.close(1000);
    this.player.stop();
    this.calls = [];
    this.queue = [];
    this.set('off');
  }

  appendAudio(pcm: ArrayBuffer) {
    if (this.wantsAudio) this.send({ type: 'input_audio_buffer.append', audio: toBase64(pcm) });
  }

  // typed text or a history tap; queued if we're still connecting
  say(text: string) {
    this.sendText(text, true);
  }

  // "Show Grok" button: hand over what the camera saw and let it react
  share(source: string, answer: string, image?: string) {
    this.trace({ kind: 'vision', title: `Showed Motion the ${source} camera`, detail: answer, image, ok: true });
    this.sendText(`[I'm showing you the ${source} camera. Vision says: ${answer}] Tell me what you think.`, false);
  }

  // live watch: slip what the camera sees into the conversation as background, no reply asked for
  observe(source: string, answer: string, image?: string) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.trace({ kind: 'vision', title: `Watching the ${source} camera`, detail: answer, image, ok: true });
    this.send({
      type: 'conversation.item.create',
      item: {
        type: 'message',
        role: 'user',
        content: [{ type: 'input_text', text: `[Live camera (${source}), no reply needed: ${answer}]` }],
      },
    });
  }

  private sendText(text: string, record: boolean) {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      this.queue.push({ text, record });
      return;
    }
    if (record) {
      this.answerPlan(text, 'typed');
      this.cmd = null; // typed requests aren't saved as voice skills
      this.onAsked?.(text, 'typed');
      this.line({ id: uid(), kind: 'user', text });
      this.trace({ kind: 'hear', title: 'Got a typed request', detail: text });
    }
    this.turnStart = Date.now();
    this.send({
      type: 'conversation.item.create',
      item: { type: 'message', role: 'user', content: [{ type: 'input_text', text }] },
    });
    this.send({ type: 'response.create' });
    this.set('thinking');
  }

  // the user's own words decide a pending plan; Grok's words never do
  private answerPlan(text: string, via: 'voice' | 'typed') {
    const r = userSaid(text, via);
    if (r === 'approved')
      this.trace({
        kind: 'plan',
        title: `You approved the plan: ${currentPlan()?.target === 'real' ? 'real arm' : 'simulation'} unlocked`,
        detail: currentPlan()?.say,
        ok: true,
      });
    if (r === 'need_target') this.trace({ kind: 'plan', title: 'Waiting for you to pick: simulation or real arm?', ok: false });
    if (r === 'rejected') this.trace({ kind: 'plan', title: 'You said no: the arm stays still', ok: false });
  }

  async emergencyStop() {
    reject(); // a stop also withdraws any approval
    this.player.stop();
    const t0 = Date.now();
    const res = await runTool('stop', {});
    this.line({ id: uid(), kind: 'tool', name: 'stop', args: 'button', ok: res.ok, ms: Date.now() - t0, at: t0 });
    if (this.ws) this.set('listening');
  }

  private handle(ev: any) {
    switch (ev.type) {
      case 'input_audio_buffer.speech_started':
        this.userLineId = null;
        this.trace({ kind: 'hear', title: 'Hearing you...' });
        break;

      case 'input_audio_buffer.speech_stopped':
        this.turnStart = Date.now();
        this.set('thinking');
        this.trace({ kind: 'think', title: 'Thinking it over' });
        break;

      case 'conversation.item.input_audio_transcription.updated':
      case 'conversation.item.input_audio_transcription.completed': {
        // `updated` (grok-transcribe) carries the cumulative transcript, not a delta. Prefer `transcript`.
        const text = typeof ev.transcript === 'string' ? ev.transcript : typeof ev.delta === 'string' ? ev.delta : '';
        if (!text.trim()) break;
        this.userLineId ??= uid();
        this.line({ id: this.userLineId, kind: 'user', text });
        this.heard = text;
        // Streaming updates only refresh the caption; approval waits for the final transcript.
        if (ev.type.endsWith('completed')) this.flushHeard();
        break;
      }

      case 'response.output_audio.delta':
      case 'response.audio.delta':
        if (!this.speakingTrace) {
          this.speakingTrace = uid();
          const ms = this.turnStart ? Date.now() - this.turnStart : undefined;
          this.trace({ id: this.speakingTrace, kind: 'speak', title: 'Answering', ms });
        }
        this.player.push(fromBase64(ev.delta));
        break;

      case 'response.output_audio_transcript.delta':
      case 'response.audio_transcript.delta': {
        const prev = this.grokLine?.kind === 'grok' ? this.grokLine.text : '';
        this.grokLine = { id: this.grokLine?.id ?? uid(), kind: 'grok', text: prev + ev.delta };
        this.line(this.grokLine);
        if (this.speakingTrace) {
          this.trace({ id: this.speakingTrace, kind: 'speak', title: 'Answering', detail: this.grokLine.text });
        }
        break;
      }

      case 'response.function_call_arguments.done': {
        this.set('acting');
        const args = ev.arguments && ev.arguments !== '{}' ? ev.arguments : undefined;
        this.trace({ kind: 'plan', title: `Decided: ${pretty(ev.name)}`, detail: args });
        this.calls.push(this.call(ev.name, ev.call_id, ev.arguments));
        break;
      }

      case 'response.done':
        this.flushHeard();
        this.grokLine = null;
        this.speakingTrace = null;
        this.finishTurn();
        break;

      case 'error':
        this.error(ev.error?.message ?? ev.message ?? 'unknown error');
        break;
    }
  }

  private flushHeard() {
    if (this.heard) {
      this.cmd = { id: uid(), text: this.heard, calls: [] }; // robot calls from here on belong to this request
      this.onAsked?.(this.heard, 'voice');
      this.trace({ kind: 'hear', title: 'Understood', detail: this.heard });
      this.answerPlan(this.heard, 'voice');
    }
    this.heard = null;
  }

  private async call(name: string, call_id: string, raw: string) {
    const id = uid();
    const t0 = Date.now();
    this.line({ id, kind: 'tool', name, args: raw, at: t0 });
    let args: Record<string, any> = {};
    try {
      args = JSON.parse(raw || '{}');
    } catch {}

    let res: Record<string, unknown> & { ok: boolean };
    const locked = needsApproval(name) && !approvedNow() && !(await waitForAnswer());
    if (name === 'propose_plan') {
      const steps = Array.isArray(args.steps) ? args.steps.map(String) : [];
      const say = String(args.say ?? steps.join(', '));
      const simOnly = !devices.arm.url;
      const places = simOnly ? { choice: false, target: 'sim' as Target } : armPlaces();
      propose(say, steps, places);
      const ask = places.choice
        ? 'ask "Shall I do it in the simulation or on the real arm?". Do NOT call arm tools or run_skill until they pick one. A plain "yes" is not enough: ask which. Spoken or typed yes/no is the approval — there is no on-screen Approve card.'
        : `ask "Shall I do it${places.target === 'real' ? ' on the real arm' : ''}?". Do NOT call arm tools or run_skill until they say yes or no. Spoken or typed yes/no is the approval — there is no on-screen Approve card.` +
          (simOnly || places.target === 'sim' ? ' The arm runs in simulation, but a yes is still required.' : '');
      res = {
        ok: true,
        status: 'waiting_for_approval',
        ...(places.choice ? { choice: ['simulation', 'real arm'] } : {}),
        ...(simOnly ? { simulated: true } : {}),
        note: `Now tell the user this plan out loud in one or two short sentences and ${ask}`,
      };
      const title = places.choice
        ? 'Plan: waiting for you to pick simulation or real arm'
        : places.target === 'real'
          ? 'Plan for the real arm: waiting for your yes'
          : 'Plan: waiting for your yes';
      this.trace({
        id,
        kind: 'plan',
        title,
        detail: [say, ...steps.map((x, i) => `${i + 1}. ${x}`)].join('\n'),
        ok: true,
        ms: Date.now() - t0,
      });
    } else if (locked) {
      const pending = currentPlan()?.state === 'pending';
      const sim = !devices.arm.url;
      res = {
        ok: false,
        needs_approval: true,
        error: pending
          ? currentPlan()?.choice
            ? 'The user has not picked yet. Ask them "In the simulation or on the real arm?" and wait for their answer.'
            : `The user has not approved the plan yet. Ask them "Shall I do it${sim ? '' : ' on the real arm'}?" and wait for yes.`
          : 'Not approved. First call propose_plan with what the arm will do, tell the user, and wait for their yes.',
      };
      this.trace({ id, kind: 'plan', title: `Held ${pretty(name)}: waiting for your approval`, ok: false, ms: Date.now() - t0 });
    } else if (name === 'camera_look') {
      this.trace({ id, kind: 'vision', title: 'Looking through the camera', detail: args.question });
      res = await this.runLook(args.question ?? 'What do you see?');
      this.trace({
        id,
        kind: 'vision',
        title: `Looked through ${res.source ?? 'camera'}`,
        detail: String(res.answer ?? res.error ?? ''),
        image: res.image as string | undefined,
        ok: res.ok,
        ms: Date.now() - t0,
      });
      delete res.image; // Grok gets the description, not the pixels
    } else if (name === 'set_full_access') {
      setFullAccess(!!args.on);
      this.trace({
        id,
        kind: 'plan',
        title: fullAccess() ? 'Full access ON: real arm for skills and tasks' : 'Full access OFF',
        ok: true,
        ms: Date.now() - t0,
      });
      res = { ok: true, full_access: fullAccess(), note: FULL_ACCESS_NOTE(fullAccess()) };
    } else if (name === 'run_skill') {
      const skill = this.skills?.list().find((s) => s.id === args.skill);
      const label = skill?.name ?? String(args.skill);
      this.trace({ id, kind: 'plan', title: `Running skill "${label}"`, detail: skill?.description });
      // running a saved skill is not a new skill: nothing from this request gets saved
      this.skipCommand();
      // approved on the real arm while the bridge runs SIM=1: run the skill on the physical arm ('robot')
      const onReal = approvedTarget() === 'real' && bridgeIsSim('arm');
      res = this.skills
        ? await this.skills.run(String(args.skill), onReal ? 'robot' : undefined)
        : { ok: false, error: 'skills unavailable' };
      this.trace({
        id,
        kind: 'tool',
        title: `Skill "${label}" ${res.ok ? 'done' : 'failed'}`,
        detail: res.ok ? undefined : String(res.error ?? res.state ?? ''),
        ok: res.ok,
        ms: Date.now() - t0,
      });
    } else if (name === 'edit_skill') {
      // the user is changing a saved skill: update that one in place, never save a new one from this request
      this.skipCommand();
      const skill = this.skills?.list().find((s) => s.id === args.skill);
      const label = skill?.name ?? String(args.skill);
      this.trace({ id, kind: 'plan', title: `Editing skill "${label}"`, detail: args.change });
      res = this.skills
        ? await this.skills.edit(String(args.skill), String(args.change ?? ''))
        : { ok: false, error: 'skills unavailable' };
      this.trace({
        id,
        kind: 'tool',
        title: `Skill "${label}" ${res.ok ? 'updated' : 'not changed'}`,
        detail: res.ok ? undefined : String(res.error ?? ''),
        ok: res.ok,
        ms: Date.now() - t0,
      });
    } else if (name.startsWith('vision_')) {
      this.trace({ id, kind: 'vision', title: `Asking vision: ${pretty(name.slice(7))}` });
      res = await this.runVision(name.slice('vision_'.length), args);
      this.trace({
        id,
        kind: 'vision',
        title: String(res.say ?? pretty(name)),
        detail: res.ok ? undefined : String(res.error ?? ''),
        ok: res.ok,
        ms: Date.now() - t0,
      });
    } else {
      this.trace({ id, kind: 'tool', title: `Running ${pretty(name)}` });
      // only arm calls of a plan approved on the real arm (SIM=1 bridge) go to the physical arm
      res = await runTool(name, args, { armReal: approvedTarget() === 'real' && bridgeIsSim('arm') });
      // a spoken request that moved a robot: tell the app (it saves this as a skill)
      if (res.ok && this.cmd && !this.cmd.skip && isRobotTool(name)) {
        this.cmd.calls.push({ name, args });
        this.onCommand?.({ ...this.cmd, calls: [...this.cmd.calls] });
      }
      this.trace({
        id,
        kind: 'tool',
        title: `${pretty(name)} ${res.ok ? 'done' : 'failed'}${res.sim ? ' (sim)' : ''}`,
        detail: res.ok ? undefined : String(res.error ?? res.status ?? ''),
        ok: res.ok,
        ms: Date.now() - t0,
        speed: speedOf(res),
      });
    }

    this.line({ id, kind: 'tool', name, args: raw, ok: res.ok, ms: Date.now() - t0, at: t0, speed: speedOf(res) });
    // skill runs report their own failures (useSkills.start); robot actions Grok ran directly are reported here
    if (!res.ok && !res.needs_approval && isRobotTool(name)) this.onToolError?.(pretty(name), String(res.error ?? res.status ?? 'failed'));
    return { call_id, output: JSON.stringify(res) };
  }

  // this request is about a saved skill: tell the app to drop anything it saved from it so far, and save nothing more
  private skipCommand() {
    if (!this.cmd || this.cmd.skip) return;
    this.cmd.skip = true;
    this.onCommand?.({ ...this.cmd, calls: [] });
  }

  private async runLook(question: string) {
    if (!this.looker) return { ok: false, error: 'no camera available' };
    try {
      return await this.looker(question);
    } catch (e: any) {
      return { ok: false, error: String(e?.message ?? e) };
    }
  }

  private async finishTurn() {
    const calls = this.calls;
    this.calls = [];

    if (!calls.length) {
      if (this.player.pending) {
        this.set('speaking');
        await this.player.flush();
      } else if (this._status !== 'off') {
        this.set('listening');
      }
      return;
    }

    // speak whatever Grok said before the tool call while the robot moves,
    // then hand results back only after playback so audio doesn't overlap
    const spoken = new Promise<void>((resolve) => {
      if (!this.player.pending) return resolve();
      const prev = this.player.onDone;
      this.player.onDone = () => {
        this.player.onDone = prev;
        resolve();
      };
      this.player.flush();
    });

    const [outputs] = await Promise.all([Promise.all(calls), spoken]);
    if (!this.ws) return;
    for (const o of outputs) {
      this.send({ type: 'conversation.item.create', item: { type: 'function_call_output', ...o } });
    }
    this.send({ type: 'response.create' });
    this.set('thinking');
  }

  private send(msg: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private set(s: Status) {
    this._status = s;
    this.on.status(s);
  }

  private line(l: Line) {
    this.on.line(l);
  }

  private trace(t: Omit<Trace, 'id' | 'at'> & { id?: string }) {
    this.on.trace({ ...t, id: t.id ?? uid(), at: Date.now() });
  }

  fail(text: string) {
    this.error(text);
  }

  private error(text: string) {
    this.line({ id: uid(), kind: 'error', text });
    this.trace({ kind: 'error', title: 'Problem', detail: text, ok: false });
  }
}

// the arm bridge reports the speed each task ran at (% of the selected robot's verified maximums)
const speedOf = (res: Record<string, unknown>) =>
  typeof res.speed_percent === 'number' ? (res.speed_percent as number) : undefined;
