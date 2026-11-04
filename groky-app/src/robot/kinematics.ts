// UR5e kinematics: forward kinematics from the official DH table and a damped least-squares IK.
// Used to keep the app's picture of the arm honest (reach, joint limits, singularities) and by the preview.
// Pure TypeScript, no React Native imports.

export type Vec3 = [number, number, number];
export type M3 = [Vec3, Vec3, Vec3]; // row-major
export type Pose = { x: number; y: number; z: number; rx: number; ry: number; rz: number }; // m + rotation vector (rad)

// UR5e standard DH (Universal Robots, "DH parameters for calculations of kinematics and dynamics")
const A = [0, -0.425, -0.3922, 0, 0, 0];
const D = [0.1625, 0, 0, 0.1333, 0.0997, 0.0996];
const ALPHA = [Math.PI / 2, 0, 0, Math.PI / 2, -Math.PI / 2, 0];

export const SHOULDER_Z = D[0];

export const rad = (deg: number) => (deg * Math.PI) / 180;
export const deg = (r: number) => (r * 180) / Math.PI;

const mul = (a: M3, b: M3): M3 =>
  [0, 1, 2].map((i) => [0, 1, 2].map((j) => a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j])) as M3;
const tr = (a: M3): M3 => [0, 1, 2].map((i) => [a[0][i], a[1][i], a[2][i]]) as M3;
const mv = (a: M3, v: Vec3): Vec3 => [0, 1, 2].map((i) => a[i][0] * v[0] + a[i][1] * v[1] + a[i][2] * v[2]) as Vec3;

export function rotvecToMat([x, y, z]: Vec3): M3 {
  const th = Math.hypot(x, y, z);
  if (th < 1e-12)
    return [
      [1, 0, 0],
      [0, 1, 0],
      [0, 0, 1],
    ];
  const [kx, ky, kz] = [x / th, y / th, z / th];
  const c = Math.cos(th);
  const s = Math.sin(th);
  const v = 1 - c;
  return [
    [kx * kx * v + c, kx * ky * v - kz * s, kx * kz * v + ky * s],
    [kx * ky * v + kz * s, ky * ky * v + c, ky * kz * v - kx * s],
    [kx * kz * v - ky * s, ky * kz * v + kx * s, kz * kz * v + c],
  ];
}

export function matToRotvec(R: M3): Vec3 {
  const th = Math.acos(Math.min(1, Math.max(-1, (R[0][0] + R[1][1] + R[2][2] - 1) / 2)));
  if (th < 1e-9) return [0, 0, 0];
  if (Math.PI - th < 1e-4) {
    // near 180°: axis from the largest diagonal entry
    const d = [R[0][0], R[1][1], R[2][2]];
    const i = d.indexOf(Math.max(...d));
    const col: Vec3 = [R[0][i], R[1][i], R[2][i]];
    col[i] += 1;
    const n = Math.hypot(...col);
    return col.map((c) => (c / n) * th) as Vec3;
  }
  const k = th / (2 * Math.sin(th));
  return [(R[2][1] - R[1][2]) * k, (R[0][2] - R[2][0]) * k, (R[1][0] - R[0][1]) * k];
}

export const poseR = (p: Pose) => rotvecToMat([p.rx, p.ry, p.rz]);
export const poseP = (p: Pose): Vec3 => [p.x, p.y, p.z];
export const makePose = (pos: Vec3, R: M3): Pose => {
  const [rx, ry, rz] = matToRotvec(R);
  return { x: pos[0], y: pos[1], z: pos[2], rx, ry, rz };
};

// rotate about the tool's own axis (URScript pose_trans with a pure rotation)
export function rotateInTool(p: Pose, axis: 0 | 1 | 2, angleRad: number): Pose {
  const v: Vec3 = [0, 0, 0];
  v[axis] = angleRad;
  return makePose(poseP(p), mul(poseR(p), rotvecToMat(v)));
}

// angle between the tool's z axis and straight down, degrees
export function toolTiltDeg(p: Pose) {
  const z = mv(poseR(p), [0, 0, 1]);
  return deg(Math.acos(Math.min(1, Math.max(-1, -z[2]))));
}

// joints in degrees -> TCP pose in the base frame
export function fk(jointsDeg: number[]): Pose {
  let R: M3 = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  let p: Vec3 = [0, 0, 0];
  for (let i = 0; i < 6; i++) {
    const q = rad(jointsDeg[i]);
    const ct = Math.cos(q);
    const st = Math.sin(q);
    const ca = Math.cos(ALPHA[i]);
    const sa = Math.sin(ALPHA[i]);
    const Ri: M3 = [
      [ct, -st * ca, st * sa],
      [st, ct * ca, -ct * sa],
      [0, sa, ca],
    ];
    const pi: Vec3 = [A[i] * ct, A[i] * st, D[i]];
    const add = mv(R, pi);
    p = [p[0] + add[0], p[1] + add[1], p[2] + add[2]];
    R = mul(R, Ri);
  }
  return makePose(p, R);
}

function solve6(M: number[][], b: number[]): number[] | null {
  const n = 6;
  const a = M.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(a[r][c]) > Math.abs(a[piv][c])) piv = r;
    if (Math.abs(a[piv][c]) < 1e-12) return null;
    [a[c], a[piv]] = [a[piv], a[c]];
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = a[r][c] / a[c][c];
      for (let k = c; k <= n; k++) a[r][k] -= f * a[c][k];
    }
  }
  return a.map((row, i) => row[n] / row[i]);
}

function poseError(target: Pose, cur: Pose): number[] {
  const eo = matToRotvec(mul(poseR(target), tr(poseR(cur))));
  return [target.x - cur.x, target.y - cur.y, target.z - cur.z, ...eo];
}

function dls(target: Pose, seed: number[]): number[] | null {
  let q = seed.map(rad);
  const fkr = (qr: number[]) => fk(qr.map(deg));
  for (let it = 0; it < 150; it++) {
    const cur = fkr(q);
    const e = poseError(target, cur);
    if (Math.hypot(e[0], e[1], e[2]) < 1e-5 && Math.hypot(e[3], e[4], e[5]) < 1e-4) return q.map(deg);
    const J: number[][] = Array.from({ length: 6 }, () => Array(6).fill(0));
    const h = 1e-6;
    for (let j = 0; j < 6; j++) {
      const dq = q.slice();
      dq[j] += h;
      const ej = poseError(fkr(dq), cur);
      for (let r = 0; r < 6; r++) J[r][j] = ej[r] / h;
    }
    const lam2 = 0.0025;
    const JtJ = Array.from({ length: 6 }, (_, i) =>
      Array.from({ length: 6 }, (_, j) => J.reduce((s, row) => s + row[i] * row[j], 0) + (i === j ? lam2 : 0)),
    );
    const Jte = Array.from({ length: 6 }, (_, i) => J.reduce((s, row, r) => s + row[i] * e[r], 0));
    const dq = solve6(JtJ, Jte);
    if (!dq) return null;
    q = q.map((v, i) => v + dq[i]);
  }
  return null;
}

// shift each joint by whole turns so it lands as close as possible to the reference
function nearest(q: number[], ref: number[]) {
  return q.map((v, i) => v + 360 * Math.round((ref[i] - v) / 360));
}

// IK near the current joints. Also tries a seed with the base already turned towards the target,
// which the solver needs when the arm swings over to the other side.
export function ik(target: Pose, current: number[]): number[] | null {
  const here = fk(current);
  const turn = deg(Math.atan2(target.y, target.x) - Math.atan2(here.y, here.x));
  const seeds = [current, current.map((v, i) => (i === 0 ? v + turn : v))];
  let best: number[] | null = null;
  let bestCost = Infinity;
  for (const s of seeds) {
    const q = dls(target, s);
    if (!q) continue;
    const n = nearest(q, current);
    const cost = n.reduce((acc, v, i) => acc + Math.abs(v - current[i]), 0);
    if (cost < bestCost) {
      best = n;
      bestCost = cost;
    }
  }
  return best;
}

export const dist3 = (a: Pose, b: Pose) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);

export function lerpPose(a: Pose, b: Pose, u: number): Pose {
  // position linear, orientation slerp-ish via relative rotation vector
  const rel = matToRotvec(mul(tr(poseR(a)), poseR(b)));
  const R = mul(poseR(a), rotvecToMat(rel.map((v) => v * u) as Vec3));
  return makePose([a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u, a.z + (b.z - a.z) * u], R);
}
