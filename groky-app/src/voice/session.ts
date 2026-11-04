import env, { SAMPLE_RATE } from '../lib/config';
import { grokTools, runTool } from '../devices';
import { fromBase64, toBase64 } from './pcm';
import { Player } from './player';
import { lookTool, PROMPT } from './prompt';
import { getToken } from './token';
import type { Line, Looker, Status, Trace } from './types';

type Events = {
  status: (s: Status) => void;
  line: (l: Line) => void;
  trace: (t: Trace) => void;
};

const pretty = (name: string) => name.replace(/_/g, ' ');

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
  private onAsked: ((text: string, via: 'voice' | 'typed') => void) | null = null;

  constructor(private on: Events) {
    this.player.onDone = () => {
      if (this._status === 'speaking') this.set('listening');
    };
  }

  setLooker(fn: Looker | null) {
    this.looker = fn;
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

  async start() {
    this.set('connecting');
    let token: string;
    try {
      token = await getToken();
    } catch (e: any) {
      this.error(e.message);
      this.set('off');
      return;
    }

    const url = `wss://api.x.ai/v1/realtime?model=${encodeURIComponent(env.voiceModel)}`;
    // RN's WebSocket accepts headers, unlike the browser one
    const ws = new (WebSocket as any)(url, null, {
      headers: { Authorization: `Bearer ${token}` },
    }) as WebSocket;
    this.ws = ws;

    ws.onopen = () => {
      this.send({
        type: 'session.update',
        session: {
          instructions: PROMPT,
          voice: env.voice,
          turn_detection: { type: 'server_vad' },
          audio: {
            input: { format: { type: 'audio/pcm', rate: SAMPLE_RATE } },
            output: { format: { type: 'audio/pcm', rate: SAMPLE_RATE } },
          },
          tools: [...grokTools(), lookTool],
        },
      });
      this.set('listening');
      this.trace({ kind: 'think', title: 'Connected to Grok Voice', detail: env.voiceModel });
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
    this.trace({ kind: 'vision', title: `Showed Grok the ${source} camera`, detail: answer, image, ok: true });
    this.sendText(`[I'm showing you the ${source} camera. Vision says: ${answer}] Tell me what you think.`, false);
  }

  private sendText(text: string, record: boolean) {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      this.queue.push({ text, record });
      return;
    }
    if (record) {
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

  async emergencyStop() {
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
        const text = ev.transcript ?? ev.delta;
        if (!text) break;
        this.userLineId ??= uid();
        this.line({ id: this.userLineId, kind: 'user', text });
        this.heard = text;
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
      this.onAsked?.(this.heard, 'voice');
      this.trace({ kind: 'hear', title: 'Understood', detail: this.heard });
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
    if (name === 'camera_look') {
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
    } else {
      this.trace({ id, kind: 'tool', title: `Running ${pretty(name)}` });
      res = await runTool(name, args);
      this.trace({
        id,
        kind: 'tool',
        title: `${pretty(name)} ${res.ok ? 'done' : 'failed'}${res.sim ? ' (sim)' : ''}`,
        detail: res.ok ? undefined : String(res.error ?? res.status ?? ''),
        ok: res.ok,
        ms: Date.now() - t0,
      });
    }

    this.line({ id, kind: 'tool', name, args: raw, ok: res.ok, ms: Date.now() - t0, at: t0 });
    return { call_id, output: JSON.stringify(res) };
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

  private error(text: string) {
    this.line({ id: uid(), kind: 'error', text });
    this.trace({ kind: 'error', title: 'Problem', detail: text, ok: false });
  }
}
