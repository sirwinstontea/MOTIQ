// Skills -> Calibration: guided, NON-ACTUATING wizard for the D435 on the UR5e flange.
// Connection -> Board setup -> Collect samples -> Solve -> Validate -> Activate -> Locate test.
// The robot is only ever moved by the operator on the teach pendant. This component never runs a skill and has no
// move/execute action: the located point can be copied, never sent to the robot.
import { ReactNode, useCallback, useEffect, useState } from 'react';
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import env from '../lib/config';
import { frameUrl } from '../lib/cameras';
import {
  calibration,
  CalibrationRecord,
  fmtMat,
  fmtXyz,
  Located,
  SessionInfo,
  Setup,
  SetupInfo,
  Status,
} from '../lib/calibration';
import { Colors, radius, shadowFloat, type, useStyles, useTheme } from '../theme';
import { Button, IconButton, Pill, Segmented } from './ui';

type Step = 'connection' | 'setup' | 'collect' | 'solve' | 'validate' | 'activate' | 'locate';
const STEPS: { id: Step; label: string }[] = [
  { id: 'connection', label: '1 Connection' },
  { id: 'setup', label: '2 Board' },
  { id: 'collect', label: '3 Collect' },
  { id: 'solve', label: '4 Solve' },
  { id: 'validate', label: '5 Validate' },
  { id: 'activate', label: '6 Activate' },
  { id: 'locate', label: '7 Locate' },
];

type Props = { onClose: () => void };

async function copyText(text: string): Promise<boolean> {
  try {
    const nav: any = (globalThis as any).navigator;
    if (nav?.clipboard?.writeText) {
      await nav.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through: the text stays selectable on screen */
  }
  return false;
}

export default function CalibrationWizard({ onClose }: Props) {
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  const [step, setStep] = useState<Step>('connection');
  const [status, setStatus] = useState<Status | null>(null);
  const [statusErr, setStatusErr] = useState<string | null>(null);
  const [setupInfo, setSetupInfo] = useState<SetupInfo | null>(null);
  const [draft, setDraft] = useState<Setup | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{ field: string; message: string }[]>([]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [result, setResult] = useState<CalibrationRecord | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [activationProblems, setActivationProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: 'ok' | 'bad' | 'warn'; text: string } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState('');
  const [label, setLabel] = useState('orange_screw_03');
  const [located, setLocated] = useState<Located | null>(null);
  const [tick, setTick] = useState(0);

  const refreshStatus = useCallback(
    () =>
      calibration.status().then((r) => {
        if (r.ok) {
          setStatus(r);
          setStatusErr(null);
        } else setStatusErr(r.reason);
      }),
    [],
  );

  useEffect(() => {
    calibration.getSetup().then((r) => {
      if (r.ok) {
        setSetupInfo(r);
        if (r.setup) setDraft(JSON.parse(JSON.stringify(r.setup)));
      } else setMsg({ tone: 'bad', text: r.reason });
    });
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // live status only on the screens that show it (it reads the robot each time)
  useEffect(() => {
    if (tick === 0 || step === 'connection' || step === 'collect') void refreshStatus();
  }, [tick, step, refreshStatus]);

  const run = async <T,>(name: string, fn: () => Promise<T>) => {
    setBusy(name);
    setMsg(null);
    try {
      return await fn();
    } finally {
      setBusy(null);
    }
  };

  const sim = status?.simulation ?? false;
  const cam = status?.camera;
  const reading = status?.robot.reading;
  const robotOk = !!reading?.valid;
  const camOk = !!cam?.connected && cam.distortion_supported !== false;
  const boardProblems = setupInfo?.board_problems ?? [];
  const canCollect = camOk && robotOk && boardProblems.length === 0 && !!setupInfo?.setup;
  const sessionId = session?.session.session_id;
  const enoughSamples = !!session && session.n_valid >= session.min_samples_to_solve;

  // --- actions ------------------------------------------------------------------------------------------------
  const saveSetup = () =>
    run('save', async () => {
      if (!draft) return;
      const r = await calibration.putSetup(draft);
      if (!r.ok) {
        setFieldErrors(r.errors ?? []);
        return setMsg({ tone: 'bad', text: r.reason });
      }
      setFieldErrors([]);
      setSetupInfo(r);
      if (r.setup) setDraft(JSON.parse(JSON.stringify(r.setup)));
      setMsg({
        tone: r.calibration_requires_revalidation ? 'warn' : 'ok',
        text: r.calibration_requires_revalidation
          ? 'Saved. The camera/robot identity or mount changed: the active calibration is no longer used. Recalibrate.'
          : 'Setup saved.',
      });
      refreshStatus();
    });

  const newSession = () =>
    run('session', async () => {
      const r = await calibration.createSession();
      if (!r.ok) return setMsg({ tone: 'bad', text: r.reason });
      setSession(r);
      setResult(null);
      setConfirmed(false);
    });

  const capture = () =>
    run('capture', async () => {
      if (!sessionId) return;
      const r = await calibration.capture(sessionId);
      if (!r.ok) {
        setMsg({ tone: 'bad', text: `Rejected (${r.status}): ${r.reason}` });
        const again = await calibration.getSession(sessionId);
        if (again.ok) setSession(again);
        return;
      }
      setSession(r);
      setMsg({ tone: 'ok', text: `Sample ${r.sample.index} saved · reprojection ${r.sample.reprojection_rmse_px.toFixed(2)} px` });
    });

  const resume = (id: string) =>
    run('session', async () => {
      const r = await calibration.getSession(id);
      if (!r.ok) return setMsg({ tone: 'bad', text: r.reason });
      setSession(r);
      setResult(r.result);
    });

  const removeSample = (i: number) =>
    run('delete', async () => {
      if (!sessionId) return;
      const r = await calibration.deleteSample(sessionId, i);
      if (r.ok) setSession(r);
    });

  const solve = () =>
    run('solve', async () => {
      if (!sessionId) return;
      const r = await calibration.solve(sessionId);
      if (!r.ok) {
        setResult(null);
        return setMsg({ tone: 'bad', text: `${r.status}: ${r.reason}` });
      }
      setResult(r.result);
      setWarnings(r.warnings);
      setActivationProblems(r.activation_problems);
      setMsg({ tone: 'ok', text: 'Provisional calibration computed. Nothing is active yet.' });
    });

  const solveFailed = async () => {
    // solve() returns ok:false when quality gates fail but still has a result to show
    if (!sessionId) return;
    const r = await calibration.getSession(sessionId);
    if (r.ok) {
      setSession(r);
      if (r.result) setResult(r.result);
    }
  };

  const validate = () =>
    run('validate', async () => {
      if (!sessionId) return;
      const r = await calibration.validate(sessionId);
      if (!r.ok) {
        setMsg({ tone: 'bad', text: `${r.status}: ${r.reason}` });
        return solveFailed();
      }
      setResult(r.result);
      setActivationProblems(r.activation_problems);
      setMsg({ tone: 'ok', text: `Independent view: ${r.validation.error_mm.toFixed(1)} mm from the fitted board` });
    });

  const activate = () =>
    run('activate', async () => {
      if (!sessionId || !confirmed) return;
      const r = await calibration.activate(sessionId, note);
      if (!r.ok) return setMsg({ tone: 'bad', text: r.reason });
      setResult(r.result);
      setMsg({ tone: 'ok', text: `Calibration ${r.calibration_id} is active.` });
      refreshStatus();
    });

  const locate = () =>
    run('locate', async () => {
      setLocated(null);
      const r = await calibration.snapshot(label.trim());
      if (!r.ok) return setMsg({ tone: 'bad', text: `${r.status}: ${r.reason}` });
      setLocated(r);
    });

  const copyCoords = async () => {
    if (!located) return;
    const [x, y, z] = located.position_base_m;
    const text = JSON.stringify({
      object_label: located.object_label,
      coordinate_frame: located.coordinate_frame,
      position_base_m: [x, y, z],
      point_semantics: located.point_semantics,
      calibration_id: located.calibration_id,
      timestamp_utc: located.timestamp_utc,
      motion_allowed: false,
    });
    const ok = await copyText(text);
    setMsg({ tone: ok ? 'ok' : 'warn', text: ok ? 'Coordinates copied.' : 'Clipboard unavailable: select the text below.' });
  };

  const copyResult = async (rec: CalibrationRecord) => {
    const ok = await copyText(JSON.stringify(rec, null, 2));
    setMsg({ tone: ok ? 'ok' : 'warn', text: ok ? 'Calibration JSON copied.' : 'Clipboard unavailable.' });
  };

  // --- render helpers ------------------------------------------------------------------------------------------
  const set = (fn: (d: Setup) => void) => {
    if (!draft) return;
    const d: Setup = JSON.parse(JSON.stringify(draft));
    fn(d);
    setDraft(d);
  };
  const errFor = (f: string) => fieldErrors.find((e) => e.field === f)?.message;

  const statusRow = (ok: boolean, title: string, detail: string) => (
    <View style={s.row} key={title}>
      <Ionicons name={ok ? 'checkmark-circle' : 'close-circle'} size={18} color={ok ? c.ok : c.bad} />
      <View style={{ flex: 1 }}>
        <Text style={s.body}>{title}</Text>
        <Text style={s.caption}>{detail}</Text>
      </View>
    </View>
  );

  const screens: Record<Step, ReactNode> = {
    connection: (
      <View style={s.gap}>
        {statusErr && <Text style={[s.body, { color: c.bad }]}>{statusErr}</Text>}
        {status &&
          statusRow(
            camOk,
            `Camera: ${cam?.model || 'not connected'}`,
            cam
              ? `serial ${cam.serial ?? 'unknown'} · ${cam.width}x${cam.height} · distortion ${cam.distortion_model}${
                  cam.distortion_supported === false ? ` (unsupported: ${cam.distortion_error})` : ''
                }`
              : 'No D435 with intrinsics',
          )}
        {status &&
          statusRow(
            robotOk,
            `Robot: ${status.robot.model ?? 'UR5e'} ${sim ? '(SIMULATION)' : ''}`,
            reading
              ? reading.valid
                ? `flange pose from ${reading.source} · ${reading.moving ? 'MOVING' : 'still'} · ${reading.age_ms} ms old` +
                  (reading.diagnostics?.fk_crosscheck_mm !== undefined
                    ? ` · FK check ${reading.diagnostics.fk_crosscheck_mm} mm`
                    : '')
                : `${reading.status}: ${reading.reason}`
              : 'No read-only pose source (start the vision server with --robot-ip)',
          )}
        {reading?.valid && reading.T_base_flange && (
          <View style={s.box}>
            <Text style={s.label}>T_base_flange (verified, m)</Text>
            <Text style={s.mono} selectable>
              {fmtMat(reading.T_base_flange)}
            </Text>
          </View>
        )}
        {env.visionServer ? (
          <Image source={{ uri: frameUrl(`${env.visionServer}/frame/arm.jpg?raw=true`, tick) }} style={s.frame} resizeMode="contain" />
        ) : null}
        {(status?.setup.flange_problems ?? []).map((p) => (
          <Text key={p} style={[s.caption, { color: c.warn }]}>
            {p}
          </Text>
        ))}
      </View>
    ),
    setup: draft ? (
      <View style={s.gap}>
        <Text style={s.label}>Board</Text>
        <Segmented
          options={[
            { id: 'chessboard', label: 'Chessboard' },
            { id: 'aruco_4', label: 'ArUco sheet' },
          ]}
          value={draft.board.type}
          onChange={(v) => set((d) => (d.board.type = v as Setup['board']['type']))}
        />
        {draft.board.type === 'chessboard' && (
          <>
            <NumField label="Square side (mm, ONE square)" value={draft.board.square_size_mm} error={errFor('board.square_size_mm')}
              onChange={(v) => set((d) => (d.board.square_size_mm = v ?? 0))} />
            <View style={s.row}>
              <NumField label="Inner corners: columns" integer value={draft.board.inner_corners_columns}
                error={errFor('board.inner_corners_columns')} onChange={(v) => set((d) => (d.board.inner_corners_columns = v))} />
              <NumField label="Inner corners: rows" integer value={draft.board.inner_corners_rows}
                error={errFor('board.inner_corners_rows')} onChange={(v) => set((d) => (d.board.inner_corners_rows = v))} />
            </View>
            <Text style={s.caption}>Count the points where four squares meet (a 9x6-square board has 8x5 inner corners).</Text>
          </>
        )}
        <Text style={[s.label, { marginTop: 8 }]}>Camera mount estimate (rough, never the calibration)</Text>
        <View style={s.row}>
          <NumField label="X longitudinal (mm)" value={draft.camera_mount_estimate.x_longitudinal_mm}
            onChange={(v) => set((d) => (d.camera_mount_estimate.x_longitudinal_mm = v))} />
          <NumField label="Y (mm, empty = unknown)" value={draft.camera_mount_estimate.y_mm}
            onChange={(v) => set((d) => (d.camera_mount_estimate.y_mm = v))} />
          <NumField label="Z (mm)" value={draft.camera_mount_estimate.z_mm}
            onChange={(v) => set((d) => (d.camera_mount_estimate.z_mm = v))} />
        </View>
        <Text style={s.caption}>Measured from</Text>
        <Segmented
          options={[
            { id: 'unconfirmed_tcp_or_flange', label: 'Unconfirmed' },
            { id: 'flange', label: 'Flange' },
            { id: 'tcp', label: 'TCP' },
          ]}
          value={draft.camera_mount_estimate.reference_frame_as_measured}
          onChange={(v) => set((d) => (d.camera_mount_estimate.reference_frame_as_measured = v as any))}
        />
        <Toggle label="X sign confirmed" value={draft.camera_mount_estimate.x_sign_confirmed}
          onChange={(v) => set((d) => (d.camera_mount_estimate.x_sign_confirmed = v))} />
        <Toggle label="Z sign confirmed" value={draft.camera_mount_estimate.z_sign_confirmed}
          onChange={(v) => set((d) => (d.camera_mount_estimate.z_sign_confirmed = v))} />
        <Text style={s.caption}>Rotation: {draft.camera_mount_estimate.rotation_rpy_deg ? draft.camera_mount_estimate.rotation_rpy_deg.join(', ') + '°' : 'unknown (not assumed zero)'}</Text>

        <Text style={[s.label, { marginTop: 8 }]}>Flange pose (read-only)</Text>
        <Segmented
          options={[
            { id: 'tcp_minus_verified_offset', label: 'TCP − verified offset' },
            { id: 'joint_fk_nominal', label: 'Joint FK (nominal)' },
          ]}
          value={draft.robot.flange_pose_method}
          onChange={(v) => set((d) => (d.robot.flange_pose_method = v as any))}
        />
        <TcpField value={draft.robot.active_tcp_offset_m_rad} error={errFor('robot.active_tcp_offset_m_rad')}
          onChange={(v) => set((d) => {
            d.robot.active_tcp_offset_m_rad = v;
            d.robot.active_tcp_offset_confirmed = false;
          })} />
        <Toggle label="The active TCP offset above matches the pendant" value={draft.robot.active_tcp_offset_confirmed}
          onChange={(v) => set((d) => (d.robot.active_tcp_offset_confirmed = v))} />
        <Text style={[s.label, { marginTop: 8 }]}>Identity</Text>
        <View style={s.row}>
          <TextField label="Camera serial" value={draft.camera.serial ?? ''} onChange={(v) => set((d) => (d.camera.serial = v.trim() || null))} />
          <TextField label="Robot serial" value={draft.robot.robot_serial ?? ''} onChange={(v) => set((d) => (d.robot.robot_serial = v.trim() || null))} />
        </View>
        {cam?.serial && draft.camera.serial !== cam.serial && (
          <Button label={`Use connected camera ${cam.serial}`} size="sm" onPress={() => set((d) => (d.camera.serial = cam.serial))} />
        )}
        <Text style={s.caption}>Mode: {draft.robot.pose_mode === 'simulation' ? 'SIMULATION' : 'physical, read-only'} (edit the JSON file to change)</Text>
        {[...boardProblems, ...(setupInfo?.board_warnings ?? []), ...(setupInfo?.flange_problems ?? [])].map((p) => (
          <Text key={p} style={[s.caption, { color: c.warn }]}>{p}</Text>
        ))}
        {fieldErrors.filter((e) => !e.field.startsWith('board.') || !errFor(e.field)).map((e) => (
          <Text key={e.field} style={[s.caption, { color: c.bad }]}>{e.field}: {e.message}</Text>
        ))}
        <Button label="Save setup" icon="save-outline" variant="primary" loading={busy === 'save'} onPress={saveSetup} />
      </View>
    ) : (
      <Text style={s.body}>Loading setup…</Text>
    ),
    collect: (
      <View style={s.gap}>
        <View style={s.notice}>
          <Ionicons name="hand-left-outline" size={18} color={c.warn} />
          <Text style={[s.body, { flex: 1 }]}>
            Move the robot manually with the teach pendant, wait until it is completely still, then press Capture. Keep
            the board fixed. Use 15–25 different poses and tilt the camera about two different axes.
          </Text>
        </View>
        {env.visionServer ? (
          <Image source={{ uri: frameUrl(`${env.visionServer}/frame/arm.jpg?raw=true`, tick) }} style={s.frame} resizeMode="contain" />
        ) : null}
        <Text style={s.caption}>
          Robot: {robotOk ? (reading?.moving ? 'MOVING' : 'still') : reading?.reason ?? 'no pose'} · Camera: {camOk ? 'ok' : 'offline'}
        </Text>
        {!sessionId ? (
          <View style={s.row}>
            <Button label="Start new session" icon="add" variant="primary" disabled={!canCollect} loading={busy === 'session'} onPress={newSession} />
            {status?.latest_session && status.latest_session.state !== 'activated' && (
              <Button label={`Resume ${status.latest_session.session_id}`} icon="refresh" onPress={() => resume(status.latest_session!.session_id)} />
            )}
          </View>
        ) : (
          <>
            <View style={s.stats}>
              <Stat label="valid" value={`${session!.n_valid}/${session!.recommended_samples}`} />
              <Stat label="rejected" value={String(session!.n_rejected)} />
              <Stat label="max rotation" value={session!.pose_diversity ? `${session!.pose_diversity.max_rotation_deg}°` : '–'} />
              <Stat label="2nd axis" value={session!.pose_diversity ? `${session!.pose_diversity.second_axis_rotation_deg}°` : '–'} />
            </View>
            <Button label="Capture sample" icon="camera-outline" variant="primary" size="lg"
              disabled={!canCollect || !!reading?.moving} loading={busy === 'capture'} onPress={capture} />
            {session!.samples.slice().reverse().map((x) => (
              <View key={x.index} style={s.row}>
                <Text style={[s.mono, { flex: 1 }]}>
                  #{x.index} · {x.reprojection_rmse_px.toFixed(2)} px · {x.n_points} pts{x.orientation_canonicalized ? ' · reordered' : ''}
                </Text>
                <IconButton icon="trash-outline" plain size={28} onPress={() => removeSample(x.index)} label="Delete sample" />
              </View>
            ))}
            {session!.recent_rejections.slice(-3).reverse().map((r) => (
              <Text key={r.at_utc} style={[s.caption, { color: c.bad }]}>{r.status}: {r.reason}</Text>
            ))}
          </>
        )}
        {!canCollect && (
          <Text style={[s.caption, { color: c.warn }]}>
            {!camOk ? 'Camera not ready. ' : ''}
            {!robotOk ? 'Verified flange pose not available. ' : ''}
            {boardProblems[0] ?? ''}
          </Text>
        )}
      </View>
    ),
    solve: (
      <View style={s.gap}>
        <Text style={s.body}>
          Computes T_flange_camera from the samples (Park–Martin), with a hold-out subset not used in the fit. Nothing is
          activated here.
        </Text>
        <Button label="Solve" icon="calculator-outline" variant="primary" disabled={!enoughSamples} loading={busy === 'solve'}
          onPress={async () => {
            await solve();
            await solveFailed();
          }} />
        {!enoughSamples && <Text style={s.caption}>Need at least {session?.min_samples_to_solve ?? 12} valid samples.</Text>}
        {result && <ResultView rec={result} onCopy={() => copyResult(result)} />}
        {warnings.map((w) => (
          <Text key={w} style={[s.caption, { color: c.warn }]}>{w}</Text>
        ))}
      </View>
    ),
    validate: (
      <View style={s.gap}>
        <Text style={s.body}>
          Leave the board where it is. Move the robot by hand to a NEW pose and press Validate: the board is measured
          again and compared with the calibrated position (not used in the fit).
        </Text>
        <Button label="Validate from this pose" icon="checkmark-done-outline" variant="primary" disabled={!result}
          loading={busy === 'validate'} onPress={validate} />
        {result?.quality.holdout_error_mm != null && (
          <Text style={s.caption}>Hold-out: mean {result.quality.holdout_error_mm} mm · max {result.quality.holdout_max_error_mm} mm ({result.quality.n_holdout} samples)</Text>
        )}
        {(result?.validation ?? []).map((v) => (
          <Text key={v.at_utc} style={[s.mono, { color: v.ok ? c.ok : c.bad }]}>
            {v.ok ? 'PASS' : 'FAIL'} · {v.error_mm.toFixed(2)} mm
          </Text>
        ))}
        {activationProblems.map((p) => (
          <Text key={p} style={[s.caption, { color: c.warn }]}>{p}</Text>
        ))}
      </View>
    ),
    activate: (
      <View style={s.gap}>
        {result ? <ResultView rec={result} onCopy={() => copyResult(result)} /> : <Text style={s.body}>Solve first.</Text>}
        {activationProblems.map((p) => (
          <Text key={p} style={[s.caption, { color: c.bad }]}>{p}</Text>
        ))}
        <Toggle
          label="I checked the quality figures and the camera has not been moved since the samples were taken."
          value={confirmed}
          onChange={setConfirmed}
        />
        <TextField label="Operator note (optional)" value={note} onChange={setNote} />
        <Button label="Activate calibration" icon="shield-checkmark-outline" variant="primary"
          disabled={!result || !confirmed || activationProblems.length > 0 || !!result.approved_for_localization}
          loading={busy === 'activate'} onPress={activate} />
        {result?.approved_for_localization && <Pill label={`Active · ${result.calibration_id}`} tone="ok" dot />}
      </View>
    ),
    locate: (
      <View style={s.gap}>
        <Text style={s.body}>
          One RGB-D shot with the robot still → the object&apos;s visible-surface centroid in the UR5e base frame. This is
          not a grasp pose and is never sent to the robot.
        </Text>
        <TextField label="Object label (class or name)" value={label} onChange={setLabel} />
        <Button label="Locate (single shot)" icon="locate-outline" variant="primary"
          disabled={!status?.active_calibration.valid || !label.trim()} loading={busy === 'locate'} onPress={locate} />
        {!status?.active_calibration.valid && (
          <Text style={[s.caption, { color: c.warn }]}>{status?.active_calibration.reason ?? 'No active calibration.'}</Text>
        )}
        {located && (
          <View style={s.box}>
            <Text style={s.label}>{located.object_label} · {located.simulation ? 'SIMULATION' : 'REAL ROBOT'}</Text>
            <Text style={s.stat} selectable>
              {fmtXyz(located.position_base_m)} mm
            </Text>
            <Text style={s.caption} selectable>
              frame {located.coordinate_frame} · {located.point_semantics} · depth {Math.round(located.depth_valid_ratio * 100)}% of{' '}
              {located.depth_points} px · {located.timestamp_utc} · {located.calibration_id} · pose {located.robot_pose_source}
            </Text>
            <Button label="Copy coordinates" icon="copy-outline" size="sm" onPress={copyCoords} />
          </View>
        )}
      </View>
    ),
  };

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={s.backdrop}>
        <SafeAreaView edges={['top', 'bottom']} style={s.wrap}>
          <View style={s.card}>
            <View style={s.head}>
              <Text style={s.title}>Camera calibration</Text>
              <Pill label={status?.mode_label ?? '…'} tone={sim ? 'warn' : 'accent'} dot />
              <IconButton icon="close" size={32} onPress={onClose} label="Close" />
            </View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.steps}>
              {STEPS.map((x) => (
                <Pressable key={x.id} onPress={() => { setStep(x.id); setMsg(null); }} style={[s.stepBtn, step === x.id && { backgroundColor: c.accent }]}>
                  <Text style={[s.stepText, { color: step === x.id ? c.accentInk : c.dim }]}>{x.label}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <ScrollView contentContainerStyle={{ paddingBottom: 8 }}>
              {screens[step]}
              {msg && (
                <Text style={[s.body, { marginTop: 10, color: msg.tone === 'ok' ? c.ok : msg.tone === 'warn' ? c.warn : c.bad }]}>
                  {msg.text}
                </Text>
              )}
            </ScrollView>
            <Text style={s.foot}>
              Read-only: this workflow never moves the robot. Camera {cam?.serial ?? '–'} · calibration{' '}
              {status?.active_calibration.record?.calibration_id ?? 'none'}
            </Text>
          </View>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

function ResultView({ rec, onCopy }: { rec: CalibrationRecord; onCopy: () => void }) {
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  const q = rec.quality;
  return (
    <View style={s.box}>
      <View style={s.row}>
        <Text style={[s.label, { flex: 1 }]}>T_flange_camera · {rec.calibration_id}</Text>
        <Pill label={rec.passes_quality_gates ? 'gates passed' : 'gates failed'} tone={rec.passes_quality_gates ? 'ok' : 'bad'} dot />
      </View>
      <Text style={s.mono} selectable>{fmtMat(rec.T_flange_camera)}</Text>
      <Text style={s.caption}>
        {q.n_fit} fit + {q.n_holdout} hold-out · PnP {q.rms_reprojection_px} px (max {q.max_reprojection_px}) · board spread{' '}
        {q.board_spread_mm} mm / {q.board_rotation_spread_deg}° · hold-out {q.holdout_error_mm ?? '–'} mm · rotation{' '}
        {q.pose_diversity.max_rotation_deg}° / 2nd axis {q.pose_diversity.second_axis_rotation_deg}°
      </Text>
      <Text style={s.caption}>
        camera {rec.camera_serial ?? '?'} · robot {rec.robot_serial ?? 'serial not set'} · {rec.robot_pose_source} ·{' '}
        {rec.simulation ? 'SIMULATION' : 'real'}
      </Text>
      <Text style={s.caption}>
        Mount estimate (not used): X {rec.mount_estimate_mm.x_longitudinal ?? '?'} · Y {rec.mount_estimate_mm.y ?? 'unknown'} · Z{' '}
        {rec.mount_estimate_mm.z ?? '?'} mm
      </Text>
      {rec.failures.map((f) => (
        <Text key={f} style={[s.caption, { color: c.bad }]}>{f}</Text>
      ))}
      <Button label="Copy calibration JSON" icon="copy-outline" size="sm" onPress={onCopy} />
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  const s = useStyles(makeStyles);
  return (
    <View style={s.statBox}>
      <Text style={s.stat}>{value}</Text>
      <Text style={s.caption}>{label}</Text>
    </View>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  return (
    <Pressable onPress={() => onChange(!value)} style={s.row}>
      <Ionicons name={value ? 'checkbox' : 'square-outline'} size={20} color={value ? c.accent : c.dim} />
      <Text style={[s.body, { flex: 1 }]}>{label}</Text>
    </Pressable>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  return (
    <View style={{ flex: 1, gap: 4 }}>
      <Text style={s.caption}>{label}</Text>
      <TextInput value={value} onChangeText={onChange} style={s.input} placeholderTextColor={c.faint} />
    </View>
  );
}

function NumField(props: {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
  integer?: boolean;
  error?: string;
}) {
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  const [text, setText] = useState(props.value == null ? '' : String(props.value));
  const [seen, setSeen] = useState(props.value);
  if (props.value !== seen) {
    // the value changed from outside (setup reloaded/saved): show it, unless it is what is being typed
    setSeen(props.value);
    const cur = text.trim() === '' ? null : Number(text.replace(',', '.'));
    if (cur !== props.value) setText(props.value == null ? '' : String(props.value));
  }
  return (
    <View style={{ flex: 1, gap: 4 }}>
      <Text style={s.caption}>{props.label}</Text>
      <TextInput
        value={text}
        keyboardType="numeric"
        placeholder="unknown"
        placeholderTextColor={c.faint}
        onChangeText={(t) => {
          setText(t);
          const raw = t.trim().replace(',', '.');
          if (raw === '') return props.onChange(null);
          const n = props.integer ? parseInt(raw, 10) : Number(raw);
          if (Number.isFinite(n)) props.onChange(n);
        }}
        style={[s.input, props.error ? { borderColor: c.bad } : null]}
      />
      {props.error && <Text style={[s.caption, { color: c.bad }]}>{props.error}</Text>}
    </View>
  );
}

// pendant shows the TCP as x, y, z in mm and rx, ry, rz in rad; the setup stores metres
function TcpField({ value, onChange, error }: { value: number[] | null; onChange: (v: number[] | null) => void; error?: string }) {
  const s = useStyles(makeStyles);
  const { c } = useTheme();
  const show = (v: number[] | null) =>
    v ? [...v.slice(0, 3).map((x) => +(x * 1000).toFixed(3)), ...v.slice(3).map((x) => +x.toFixed(6))].join(', ') : '';
  const [text, setText] = useState(show(value));
  const [bad, setBad] = useState<string | null>(null);
  return (
    <View style={{ gap: 4 }}>
      <Text style={s.caption}>Active TCP offset from the pendant: x, y, z (mm), rx, ry, rz (rad)</Text>
      <TextInput
        value={text}
        placeholder="e.g. 0, 0, 180, 0, 0, 0   (empty = not entered)"
        placeholderTextColor={c.faint}
        onChangeText={(t) => {
          setText(t);
          if (!t.trim()) {
            setBad(null);
            return onChange(null);
          }
          const n = t.split(/[,;\s]+/).filter(Boolean).map(Number);
          if (n.length !== 6 || n.some((x) => !Number.isFinite(x))) return setBad('needs 6 numbers');
          setBad(null);
          onChange([n[0] / 1000, n[1] / 1000, n[2] / 1000, n[3], n[4], n[5]]);
        }}
        style={[s.input, bad || error ? { borderColor: c.bad } : null]}
      />
      {(bad || error) && <Text style={[s.caption, { color: c.bad }]}>{bad ?? error}</Text>}
    </View>
  );
}

const makeStyles = (c: Colors) =>
  StyleSheet.create({
    backdrop: { flex: 1, backgroundColor: c.scrim },
    wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 16 },
    card: {
      width: '100%',
      maxWidth: 760,
      maxHeight: '100%',
      flexShrink: 1,
      backgroundColor: c.panel,
      borderRadius: radius.lg,
      padding: 20,
      boxShadow: shadowFloat,
    },
    head: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
    title: { ...type.h2, color: c.text, flex: 1 },
    steps: { gap: 6, paddingBottom: 12 },
    stepBtn: { paddingHorizontal: 10, height: 28, borderRadius: radius.sm, justifyContent: 'center', backgroundColor: c.panelHi },
    stepText: { ...type.caption, fontSize: 12 },
    gap: { gap: 10 },
    row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    box: { backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12, gap: 8 },
    notice: { flexDirection: 'row', gap: 10, backgroundColor: c.panelHi, borderRadius: radius.md, padding: 12 },
    frame: { width: '100%', aspectRatio: 4 / 3, backgroundColor: c.page, borderRadius: radius.md },
    label: { ...type.overline, color: c.faint },
    body: { ...type.body, color: c.text },
    caption: { ...type.caption, color: c.dim },
    mono: { ...type.mono, color: c.text },
    stat: { ...type.stat, color: c.text },
    stats: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
    statBox: { flexGrow: 1, minWidth: 110, backgroundColor: c.panelHi, borderRadius: radius.md, padding: 10 },
    input: {
      backgroundColor: c.panel,
      borderRadius: radius.md,
      borderWidth: 1,
      borderColor: c.lineStrong,
      paddingHorizontal: 12,
      height: 40,
      ...type.body,
      color: c.text,
    },
    foot: { ...type.caption, color: c.faint, marginTop: 10 },
  });
