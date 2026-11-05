// Skills -> Calibration client (computer-vision server, ../../computer-vision/vision/eyes/server/calibration_api.py).
//
// NON-ACTUATING by construction: this module only talks to /calibration/* and /localization/snapshot on the vision
// server. It never imports the skill runner, never calls a robot/bridge motion endpoint, and nothing it returns is a
// robot target. The operator moves the UR5e by hand from the teach pendant between captures.
import env from './config';

export type Mat4 = number[][];

export type Fail = { ok: false; status: string; reason: string; errors?: { field: string; message: string }[] };
export type Res<T> = (T & { ok: true; status: string }) | Fail;

export type Setup = {
  schema_version: string;
  robot: {
    model: 'UR5e';
    pose_mode: 'physical_read_only' | 'simulation';
    calibration_frame: 'flange';
    robot_serial: string | null;
    flange_pose_method: 'tcp_minus_verified_offset' | 'joint_fk_nominal';
    active_tcp_offset_m_rad: number[] | null;
    active_tcp_offset_confirmed: boolean;
    fk_crosscheck_tol_mm: number;
    fk_crosscheck_tol_deg: number;
  };
  camera: { model: string; serial: string | null; coordinate_frame: 'camera_color_optical' };
  camera_mount_estimate: {
    reference_frame_as_measured: 'unconfirmed_tcp_or_flange' | 'flange' | 'tcp';
    x_longitudinal_mm: number | null;
    y_mm: number | null;
    z_mm: number | null;
    x_sign_confirmed: boolean;
    z_sign_confirmed: boolean;
    rotation_rpy_deg: number[] | null;
    status: 'rough_estimate_only';
    use_as_calibration: false;
  };
  board: {
    type: 'chessboard' | 'aruco_4';
    square_size_mm: number;
    inner_corners_columns: number | null;
    inner_corners_rows: number | null;
    orientation_reference_confirmed: boolean;
  };
  capture: Record<string, unknown> & { recommended_samples: number; min_samples_to_solve: number };
  localization: Record<string, unknown>;
  quality_gates: Record<string, number>;
};

export type SetupInfo = {
  source_file: string;
  setup: Setup | null;
  setup_sha256: string | null;
  board_problems: string[];
  board_warnings: string[];
  flange_problems: string[];
  mount_notes: string[];
  calibration_requires_revalidation?: boolean;
};

export type CalibrationRecord = {
  calibration_id: string;
  session_id: string;
  created_at_utc: string;
  approved_at_utc?: string;
  approved_for_localization: boolean;
  passes_quality_gates: boolean;
  failures: string[];
  quality: {
    n_samples: number;
    n_fit: number;
    n_holdout: number;
    rms_reprojection_px: number;
    max_reprojection_px: number;
    handeye_reprojection_rms_px: number;
    board_spread_mm: number;
    board_rotation_spread_deg: number;
    holdout_error_mm: number | null;
    holdout_max_error_mm: number | null;
    pose_diversity: { max_rotation_deg: number; second_axis_rotation_deg: number };
  };
  T_flange_camera: Mat4;
  camera_serial: string | null;
  robot_serial: string | null;
  robot_pose_source: string | null;
  simulation: boolean;
  image_size: [number, number];
  mount_estimate_mm: { x_longitudinal: number | null; y: number | null; z: number | null; used_as_calibration: false };
  validation: { at_utc: string; error_mm: number; ok: boolean }[];
};

export type CardState = 'Not calibrated' | 'Collecting samples' | 'Pending validation' | 'Calibrated' | 'Error';

export type Status = {
  card_state: CardState;
  simulation: boolean;
  mode_label: 'SIMULATION' | 'REAL ROBOT';
  setup: { valid: boolean; source_file: string; errors: { field: string; message: string }[] } & Omit<
    SetupInfo,
    'setup' | 'setup_sha256' | 'source_file'
  >;
  camera: null | {
    connected: boolean;
    model: string;
    serial: string | null;
    simulation: boolean;
    width: number;
    height: number;
    fx: number;
    fy: number;
    distortion_model: string;
    distortion_supported: boolean;
    distortion_error?: string;
  };
  robot: {
    provider: null | { provider: string; robot_ip?: string; simulation: boolean; connected: boolean; last_error: string | null };
    reading: null | {
      valid: boolean;
      status: string;
      reason: string | null;
      source?: string;
      moving?: boolean;
      age_ms?: number;
      T_base_flange?: Mat4 | null;
      diagnostics?: Record<string, unknown>;
    };
    model: string | null;
    serial: string | null;
  };
  active_calibration: { valid: boolean; status: string; reason: string | null; record: CalibrationRecord | null };
  latest_session: { session_id: string; state: string } | null;
  motion_allowed: false;
};

export type SessionInfo = {
  session: { session_id: string; state: string; simulation: boolean; robot_pose_source: string | null; camera: { serial: string | null } };
  samples: { index: number; frame_id: number; reprojection_rmse_px: number; n_points: number; orientation_canonicalized: boolean }[];
  n_valid: number;
  n_rejected: number;
  recent_rejections: { at_utc: string; status: string; reason: string }[];
  pose_diversity: { max_rotation_deg: number; second_axis_rotation_deg: number } | null;
  recommended_samples: number;
  min_samples_to_solve: number;
  result: CalibrationRecord | null;
};

export type SolveInfo = { result: CalibrationRecord; warnings: string[]; activation_problems: string[] };

export type Located = {
  object_label: string;
  object_class: string;
  confidence: number;
  timestamp_utc: string;
  frame_id: string;
  coordinate_frame: 'ur5e_base';
  position_camera_m: [number, number, number];
  position_base_m: [number, number, number];
  point_semantics: 'visible_surface_centroid_3d';
  depth_valid_ratio: number;
  depth_points: number;
  robot_pose_source: string;
  simulation: boolean;
  calibration_id: string;
  motion_allowed: false;
};

export const calibrationReady = () => !!env.visionServer;

async function call<T>(method: 'GET' | 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<Res<T>> {
  if (!env.visionServer) return { ok: false, status: 'vision_offline', reason: 'EXPO_PUBLIC_VISION_SERVER is not set' };
  if (!path.startsWith('/calibration/') && path !== '/localization/snapshot') {
    return { ok: false, status: 'bad_request', reason: 'not a calibration route' }; // defence in depth
  }
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (env.bridgeKey) headers['X-Bridge-Key'] = env.bridgeKey;
  try {
    const r = await fetch(`${env.visionServer}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok !== true) {
      // an error is never shown as success: anything but an explicit ok:true is a failure
      return {
        ok: false,
        status: String(j.status ?? `http_${r.status}`),
        reason: String(j.reason ?? j.detail ?? `HTTP ${r.status}`),
        errors: j.errors,
      };
    }
    return j as Res<T>;
  } catch (e: any) {
    return { ok: false, status: 'vision_offline', reason: `Can't reach the vision server: ${e?.message ?? e}` };
  }
}

const sid = (id: string) => encodeURIComponent(id);

export const calibration = {
  status: () => call<Status>('GET', '/calibration/status'),
  getSetup: () => call<SetupInfo>('GET', '/calibration/setup'),
  putSetup: (setup: Setup) => call<SetupInfo>('PUT', '/calibration/setup', setup),
  createSession: () => call<SessionInfo>('POST', '/calibration/sessions'),
  getSession: (id: string) => call<SessionInfo>('GET', `/calibration/sessions/${sid(id)}`),
  capture: (id: string) => call<SessionInfo & { sample: { index: number; reprojection_rmse_px: number } }>(
    'POST',
    `/calibration/sessions/${sid(id)}/capture`,
  ),
  deleteSample: (id: string, index: number) =>
    call<SessionInfo>('DELETE', `/calibration/sessions/${sid(id)}/samples/${Math.trunc(index)}`),
  solve: (id: string) => call<SolveInfo>('POST', `/calibration/sessions/${sid(id)}/solve`),
  validate: (id: string) =>
    call<{ validation: { error_mm: number; ok: boolean }; result: CalibrationRecord; activation_problems: string[] }>(
      'POST',
      `/calibration/sessions/${sid(id)}/validate`,
    ),
  activate: (id: string, operatorNote: string) =>
    call<{ calibration_id: string; result: CalibrationRecord }>('POST', `/calibration/sessions/${sid(id)}/activate`, {
      confirm: true,
      operator_note: operatorNote,
    }),
  snapshot: (objectLabel: string) =>
    call<Located>('POST', '/localization/snapshot', { source: 'arm', object_label: objectLabel, single_frame: true }),
};

export const fmtMat = (m: Mat4 | null | undefined) =>
  (m ?? []).map((row) => row.map((v) => (v >= 0 ? ' ' : '') + v.toFixed(5)).join('  ')).join('\n');

export const fmtXyz = (p: number[], unit: 'm' | 'mm' = 'mm') =>
  p.map((v) => (unit === 'mm' ? (v * 1000).toFixed(1) : v.toFixed(4))).join(', ');
