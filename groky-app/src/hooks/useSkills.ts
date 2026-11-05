import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { devices, stopDevice, ToolResult } from '../devices';
import {
  approved,
  builtinSkills,
  contentSignature,
  deviceOf,
  findDuplicate,
  fromV1,
  revoked,
  SkillDoc,
  touched,
} from '../skills';
import { skillFromVoice } from '../skills/fromVoice';
import type { VoiceCommand } from '../skills/fromVoice';
import { generateSkill } from '../skills/generate';
import { nameSkill } from '../skills/nameSkill';
import { deleteLearned, fetchLearned, isLearned, storeLearned } from '../skills/store';
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

// Older versions saved a new voice skill every time a saved skill was mentioned or run by voice. Drop those
// automatic copies on load (voice-made skills that do exactly what an earlier skill does). Hand-made skills are
// never removed.
function dropVoiceCopies(saved: SkillDoc[]): SkillDoc[] {
  const kept: SkillDoc[] = [...builtinSkills];
  return saved.filter((s) => {
    if (s.skill.source?.type === 'voice' && findDuplicate(s, kept)) return false;
    kept.push(s);
    return true;
  });
}

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
        if (s) setCustom(dropVoiceCopies(JSON.parse(s)));
        else if (old) setCustom(JSON.parse(old).map(fromV1)); // one-time upgrade from the old flat format
        if (st) setStats(JSON.parse(st));
        if (rv) setReviews(JSON.parse(rv));
      })
      .catch(() => {})
      .finally(() => {
        loaded.current = true;
        // learned skills kept on the robot PC (teach server) that this tablet doesn't have yet
        fetchLearned().then((pc) => {
          if (!pc.length) return;
          setCustom((prev) => {
            const have = new Set(prev.map((s) => s.skill.id));
            const add = pc.filter((d) => d?.skill?.id && !have.has(d.skill.id) && !findDuplicate(d, [...builtinSkills, ...prev]));
            return add.length ? [...prev, ...add] : prev;
          });
        });
      });
  }, []);

  // every learned skill (and each new revision of it) is also saved on the robot PC
  const synced = useRef<Record<string, string>>({});
  useEffect(() => {
    if (!loaded.current) return;
    for (const d of custom) {
      if (!isLearned(d)) continue;
      const sig = `${d.skill.updated_at}|${d.review?.revision}|${d.review?.approved}`;
      if (synced.current[d.skill.id] === sig) continue;
      synced.current[d.skill.id] = sig;
      storeLearned(d);
    }
  }, [custom]);

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

  // A saved edit is a new revision and needs approving again. Only the skill document is stored, and never a
  // second copy of a skill that already does the same thing: returns that skill instead (nothing saved).
  const save = useCallback(
    (doc: SkillDoc): SkillDoc | undefined => {
      const before = skillsRef.current.find((s) => s.skill.id === doc.skill.id);
      const dup = findDuplicate(doc, skillsRef.current);
      // (renaming a skill that was already a copy is fine; making it a copy is not)
      if (dup && (!before || contentSignature(before) !== contentSignature(doc))) {
        notify({ kind: 'warn', title: 'Not saved: already in Skills', body: `"${dup.skill.name}" does exactly the same thing.` });
        return dup;
      }
      const next = touched({ ...doc, app: { ...doc.app, builtin: false } });
      setCustom((prev) =>
        prev.some((s) => s.skill.id === doc.skill.id)
          ? prev.map((s) => (s.skill.id === doc.skill.id ? next : s))
          : [...prev, next],
      );
      return undefined;
    },
    [notify],
  );

  const remove = useCallback((id: string) => {
    const doc = skillsRef.current.find((s) => s.skill.id === id);
    if (doc && isLearned(doc)) deleteLearned(id);
    setCustom((prev) => prev.filter((s) => s.skill.id !== id));
  }, []);

  // A spoken request moved a robot: keep what ran as a skill in the Skills tab (tap it to run it again).
  // Called again as more actions of the same request finish, so it updates one skill. Never saved: a request that
  // ran or edited a saved skill (cmd.skip), or one that does exactly what a saved skill already does.
  // Grok-written names for voice skills, by skill id (kept across the updates of one request)
  const aiNames = useRef<Record<string, string>>({});
  const nameTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const giveName = useCallback((doc: SkillDoc, said: string) => {
    const id = doc.skill.id;
    clearTimeout(nameTimers.current[id]);
    // wait a moment so the later actions of the same request are in the skill before it is named
    nameTimers.current[id] = setTimeout(async () => {
      const name = await nameSkill(doc, said);
      if (!name) return;
      aiNames.current[id] = name;
      setCustom((prev) => prev.map((s) => (s.skill.id === id ? { ...s, skill: { ...s.skill, name } } : s)));
    }, 1500);
  }, []);

  const saveVoice = useCallback(
    (cmd: VoiceCommand) => {
      const id = `voice_${cmd.id}`;
      // drop the partial skill this same request may have saved already
      const drop = () => {
        clearTimeout(nameTimers.current[id]);
        setCustom((prev) => prev.filter((s) => s.skill.id !== id));
      };
      if (cmd.skip) return drop();
      const doc = skillFromVoice(cmd, id);
      if (!doc) return;
      if (findDuplicate(doc, skillsRef.current)) return drop();
      const isNew = !skillsRef.current.some((s) => s.skill.id === id);
      setCustom((prev) =>
        prev.some((s) => s.skill.id === id)
          ? prev.map((s) =>
              s.skill.id === id
                ? { ...doc, skill: { ...doc.skill, created_at: s.skill.created_at, name: aiNames.current[id] ?? doc.skill.name } }
                : s,
            )
          : [...prev, doc],
      );
      giveName(doc, cmd.text);
      if (isNew)
        notify({ kind: 'ok', title: 'Saved to Skills', body: `"${doc.skill.name}": tap it in the Skills tab to run it again`, silent: true });
    },
    [notify, giveName],
  );

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
      // a skill that can't even start is an error notification too (voice and Show Motion both run skills here)
      const refuse = (error: string, name = id) => {
        notify({ kind: 'bad', title: `Couldn't run "${name}"`, body: error });
        return { ok: false, error };
      };
      if (!doc) return refuse(`no skill called ${id}`);
      if (token.current) return refuse('another skill is already running', doc.skill.name);

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

  // The user asked (by voice) to change a saved skill: Grok rewrites that skill and it is saved over itself, same id.
  // Never creates a new skill. Like any edit it needs approving again before it moves the real robot.
  const edit = useCallback(
    async (id: string, change: string): Promise<ToolResult> => {
      const doc = find(id);
      if (!doc) return { ok: false, error: `no skill called ${id}` };
      if (doc.app?.builtin)
        return { ok: false, error: `"${doc.skill.name}" is built-in and read-only: duplicate it in the Skills tab to change it` };
      if (!change.trim()) return { ok: false, error: 'no change given' };
      if (token.current) return { ok: false, error: 'a skill is running right now, change it after it finishes' };
      try {
        const out = await generateSkill(change, doc);
        if (out.problems.length)
          return { ok: false, error: `skill not changed, Grok's version has a problem: ${out.problems[0]}` };
        const next: SkillDoc = { ...out.doc, skill: { ...out.doc.skill, id } };
        if (contentSignature(next) === contentSignature(doc) && next.skill.name === doc.skill.name)
          return { ok: true, changed: false, skill: doc.skill.name, note: 'the skill already does that, nothing changed' };
        const dup = save(next);
        if (dup) return { ok: false, error: `that would make it the same as the saved skill "${dup.skill.name}"` };
        notify({
          kind: 'ok',
          title: `Updated "${next.skill.name}"`,
          body: 'Changed by voice. Approve it again in the Skills tab before it runs on the real robot.',
          silent: true,
        });
        return {
          ok: true,
          changed: true,
          skill: next.skill.name,
          steps: next.steps.map((s) => s.description),
          uncertainties: next.interpretation?.uncertainties ?? [],
          needs_approval: true,
        };
      } catch (e: any) {
        return { ok: false, error: String(e?.message ?? e) };
      }
    },
    [find, save, notify],
  );

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
    saveVoice,
    edit,
    remove,
    approve,
    revoke,
    start,
    stop,
    getSkills,
  };
}

export type Skills = ReturnType<typeof useSkills>;
