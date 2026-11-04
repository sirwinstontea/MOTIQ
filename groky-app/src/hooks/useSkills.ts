import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { devices, stopDevice, ToolResult } from '../devices';
import { approved, builtinSkills, deviceOf, fromV1, revoked, SkillDoc, touched } from '../skills';
import { Progress, runSkill, StepResult, Token } from '../skills/runner';
import { useNotify } from './useNotify';

export type { StepResult };

export type Run = {
  skillId: string;
  state: 'running' | 'done' | 'failed' | 'stopped';
  step: number;
  prim: number | null;
  results: StepResult[];
  startedAt: number;
  endedAt?: number;
  via: 'tap' | 'voice';
  error?: string;
};

export type SkillStats = { runs: number; ok: number; lastAt?: number; lastOk?: boolean };

const SKILLS_KEY = 'skills-v2';
const OLD_SKILLS_KEY = 'skills-v1';
const STATS_KEY = 'skill-stats-v1';
// approvals of built-in skills (their files are read-only)
const REVIEWS_KEY = 'skill-reviews-v1';

type Review = Pick<SkillDoc, 'review' | 'safety' | 'skill'>;

export function useSkills() {
  const { notify } = useNotify();
  const [custom, setCustom] = useState<SkillDoc[]>([]);
  const [reviews, setReviews] = useState<Record<string, Review>>({});
  const [stats, setStats] = useState<Record<string, SkillStats>>({});
  const [run, setRun] = useState<Run | null>(null);
  const loaded = useRef(false);
  const token = useRef<Token | null>(null);

  useEffect(() => {
    Promise.all([SKILLS_KEY, OLD_SKILLS_KEY, STATS_KEY, REVIEWS_KEY].map((k) => AsyncStorage.getItem(k)))
      .then(([s, old, st, rv]) => {
        if (s) setCustom(JSON.parse(s));
        else if (old) setCustom(JSON.parse(old).map(fromV1)); // one-time upgrade from the old flat format
        if (st) setStats(JSON.parse(st));
        if (rv) setReviews(JSON.parse(rv));
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
  useEffect(() => {
    if (loaded.current) AsyncStorage.setItem(REVIEWS_KEY, JSON.stringify(reviews)).catch(() => {});
  }, [reviews]);

  const skills = useMemo(
    () => [
      ...builtinSkills.map((b) => {
        const r = reviews[b.skill.id];
        return r ? { ...b, skill: { ...b.skill, status: r.skill.status }, review: r.review, safety: r.safety } : b;
      }),
      ...custom,
    ],
    [custom, reviews],
  );
  const skillsRef = useRef(skills);
  useEffect(() => {
    skillsRef.current = skills;
  }, [skills]);

  const find = useCallback((id: string) => skillsRef.current.find((s) => s.skill.id === id), []);

  // a saved edit is a new revision and needs approving again
  const save = useCallback((doc: SkillDoc) => {
    const next = touched({ ...doc, app: { ...doc.app, builtin: false } });
    setCustom((prev) =>
      prev.some((s) => s.skill.id === doc.skill.id)
        ? prev.map((s) => (s.skill.id === doc.skill.id ? next : s))
        : [...prev, next],
    );
  }, []);

  const remove = useCallback((id: string) => setCustom((prev) => prev.filter((s) => s.skill.id !== id)), []);

  const setDoc = useCallback((doc: SkillDoc) => {
    if (doc.app?.builtin) {
      setReviews((prev) => ({ ...prev, [doc.skill.id]: { review: doc.review, safety: doc.safety, skill: doc.skill } }));
    } else {
      setCustom((prev) => prev.map((s) => (s.skill.id === doc.skill.id ? doc : s)));
    }
  }, []);

  const approve = useCallback(
    (id: string, physical: boolean) => {
      const doc = find(id);
      if (doc) setDoc(approved(doc, 'operator', physical));
    },
    [find, setDoc],
  );
  const revoke = useCallback(
    (id: string) => {
      const doc = find(id);
      if (doc) setDoc(revoked(doc));
    },
    [find, setDoc],
  );

  const start = useCallback(
    async (id: string, via: Run['via'] = 'tap'): Promise<ToolResult> => {
      const doc = find(id);
      if (!doc) return { ok: false, error: `no skill called ${id}` };
      if (token.current) return { ok: false, error: 'another skill is already running' };

      const me: Token = { cancelled: false };
      token.current = me;
      const startedAt = Date.now();
      const device = deviceOf(doc);
      const real = !!devices[device].url;
      setRun({ skillId: id, state: 'running', step: 0, prim: null, results: [], startedAt, via });
      notify({
        kind: 'info',
        title: `Running "${doc.skill.name}"`,
        body: `${doc.steps.length} steps on the ${devices[device].label.toLowerCase()}${real ? '' : ' (simulated)'}`,
        silent: via === 'voice',
      });

      const out = await runSkill(doc, me, {
        progress: (p: Progress) => setRun((r) => r && { ...r, step: p.step, prim: p.prim }),
        result: (i, res) =>
          setRun((r) => {
            if (!r) return r;
            const results = r.results.slice();
            results[i] = res;
            return { ...r, results };
          }),
      });

      const endedAt = Date.now();
      token.current = null;
      setRun((r) => r && { ...r, state: out.state, endedAt, error: out.error });
      if (out.state !== 'stopped') {
        setStats((prev) => {
          const p = prev[id] ?? { runs: 0, ok: 0 };
          const ok = out.state === 'done';
          return { ...prev, [id]: { runs: p.runs + 1, ok: p.ok + (ok ? 1 : 0), lastAt: endedAt, lastOk: ok } };
        });
      }

      const secs = ((endedAt - startedAt) / 1000).toFixed(1);
      const name = doc.skill.name;
      if (out.state === 'done')
        notify({ kind: 'ok', title: `"${name}" finished`, body: `All ${doc.steps.length} steps in ${secs}s` });
      if (out.state === 'failed') notify({ kind: 'bad', title: `"${name}" failed`, body: out.error });
      if (out.state === 'stopped')
        notify({
          kind: 'warn',
          title: `"${name}" stopped`,
          body: `Stopped after ${out.results.length} of ${doc.steps.length} steps`,
        });

      return {
        ok: out.ok,
        skill: name,
        state: out.state,
        steps: out.results.length,
        total: doc.steps.length,
        sim: !real || undefined,
        error: out.error,
      };
    },
    [find, notify],
  );

  // cancel the loop and tell the hardware to halt the current step
  const stop = useCallback(async () => {
    const me = token.current;
    if (!me) return;
    me.cancelled = true;
    const doc = run && find(run.skillId);
    if (doc) await stopDevice(deviceOf(doc));
  }, [run, find]);

  // what Grok sees for run_skill
  const getSkills = useCallback(
    () =>
      skillsRef.current.map((s) => ({
        id: s.skill.id,
        name: s.skill.name,
        description: s.intent?.natural_language_instruction || s.skill.description,
      })),
    [],
  );

  return {
    skills,
    stats,
    run,
    running: run?.state === 'running',
    save,
    remove,
    approve,
    revoke,
    start,
    stop,
    getSkills,
  };
}

export type Skills = ReturnType<typeof useSkills>;
