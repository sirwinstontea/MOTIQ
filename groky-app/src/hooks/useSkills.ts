import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { devices, runTool, stopDevice, ToolResult } from '../devices';
import { builtinSkills, Skill, WAIT } from '../skills';
import { useNotify } from './useNotify';

export type StepResult = { ok: boolean; ms: number; error?: string };

export type Run = {
  skillId: string;
  state: 'running' | 'done' | 'failed' | 'stopped';
  step: number;
  results: StepResult[];
  startedAt: number;
  endedAt?: number;
  via: 'tap' | 'voice';
};

export type SkillStats = { runs: number; ok: number; lastAt?: number; lastOk?: boolean };

const SKILLS_KEY = 'skills-v1';
const STATS_KEY = 'skill-stats-v1';

const wait = (ms: number, token: { cancelled: boolean }) =>
  new Promise<void>((resolve) => {
    const end = Date.now() + ms;
    const tick = () => (token.cancelled || Date.now() >= end ? resolve() : setTimeout(tick, 100));
    tick();
  });

export function useSkills() {
  const { notify } = useNotify();
  const [custom, setCustom] = useState<Skill[]>([]);
  const [stats, setStats] = useState<Record<string, SkillStats>>({});
  const [run, setRun] = useState<Run | null>(null);
  const loaded = useRef(false);
  const token = useRef<{ cancelled: boolean } | null>(null);

  useEffect(() => {
    Promise.all([AsyncStorage.getItem(SKILLS_KEY), AsyncStorage.getItem(STATS_KEY)])
      .then(([s, st]) => {
        if (s) setCustom(JSON.parse(s));
        if (st) setStats(JSON.parse(st));
      })
      .catch(() => {})
      .finally(() => (loaded.current = true));
  }, []);

  useEffect(() => {
    if (loaded.current) AsyncStorage.setItem(SKILLS_KEY, JSON.stringify(custom)).catch(() => {});
  }, [custom]);

  useEffect(() => {
    if (loaded.current) AsyncStorage.setItem(STATS_KEY, JSON.stringify(stats)).catch(() => {});
  }, [stats]);

  const skills = useMemo(() => [...builtinSkills, ...custom], [custom]);
  const skillsRef = useRef(skills);
  useEffect(() => {
    skillsRef.current = skills;
  }, [skills]);

  const save = useCallback((skill: Skill) => {
    setCustom((prev) =>
      prev.some((s) => s.id === skill.id) ? prev.map((s) => (s.id === skill.id ? skill : s)) : [...prev, skill],
    );
  }, []);

  const remove = useCallback((id: string) => setCustom((prev) => prev.filter((s) => s.id !== id)), []);

  const start = useCallback(
    async (id: string, via: Run['via'] = 'tap'): Promise<ToolResult> => {
      const skill = skillsRef.current.find((s) => s.id === id);
      if (!skill) return { ok: false, error: `no skill called ${id}` };
      if (token.current) return { ok: false, error: 'another skill is already running' };

      const me = { cancelled: false };
      token.current = me;
      const startedAt = Date.now();
      const results: StepResult[] = [];
      setRun({ skillId: id, state: 'running', step: 0, results: [], startedAt, via });
      notify({
        kind: 'info',
        title: `Running "${skill.name}"`,
        body: `${skill.steps.length} steps on the ${devices[skill.device].label.toLowerCase()}`,
        silent: via === 'voice',
      });

      let failed: string | undefined;
      for (let i = 0; i < skill.steps.length && !me.cancelled; i++) {
        setRun((r) => r && { ...r, step: i });
        const step = skill.steps[i];
        const t0 = Date.now();
        let res: ToolResult;
        if (step.tool === WAIT) {
          await wait(Number(step.args.seconds ?? 1) * 1000, me);
          res = { ok: true };
        } else {
          res = await runTool(step.tool, step.args);
        }
        if (me.cancelled) break;
        const r: StepResult = { ok: res.ok, ms: Date.now() - t0 };
        if (!res.ok) r.error = String(res.error ?? res.status ?? 'failed');
        results.push(r);
        setRun((prev) => prev && { ...prev, results: [...results] });
        if (!res.ok) {
          failed = `step ${i + 1} (${step.tool.replace(/_/g, ' ')}): ${r.error}`;
          break;
        }
      }

      const state: Run['state'] = me.cancelled ? 'stopped' : failed ? 'failed' : 'done';
      const endedAt = Date.now();
      token.current = null;
      setRun((r) => r && { ...r, state, endedAt });
      if (state !== 'stopped') {
        setStats((prev) => {
          const p = prev[id] ?? { runs: 0, ok: 0 };
          return {
            ...prev,
            [id]: {
              runs: p.runs + 1,
              ok: p.ok + (state === 'done' ? 1 : 0),
              lastAt: endedAt,
              lastOk: state === 'done',
            },
          };
        });
      }

      const secs = ((endedAt - startedAt) / 1000).toFixed(1);
      if (state === 'done')
        notify({ kind: 'ok', title: `"${skill.name}" finished`, body: `All ${skill.steps.length} steps in ${secs}s` });
      if (state === 'failed') notify({ kind: 'bad', title: `"${skill.name}" failed`, body: failed });
      if (state === 'stopped')
        notify({
          kind: 'warn',
          title: `"${skill.name}" stopped`,
          body: `Stopped after ${results.length} of ${skill.steps.length} steps`,
        });

      return {
        ok: state === 'done',
        skill: skill.name,
        state,
        steps: results.length,
        total: skill.steps.length,
        error: failed,
      };
    },
    [notify],
  );

  // cancel the loop and tell the hardware to halt the current step
  const stop = useCallback(async () => {
    const me = token.current;
    if (!me) return;
    me.cancelled = true;
    const skill = skillsRef.current.find((s) => s.id === run?.skillId);
    if (skill) await stopDevice(skill.device);
  }, [run?.skillId]);

  const getSkills = useCallback(() => skillsRef.current, []);

  return {
    skills,
    stats,
    run,
    running: run?.state === 'running',
    save,
    remove,
    start,
    stop,
    getSkills,
  };
}

export type Skills = ReturnType<typeof useSkills>;
