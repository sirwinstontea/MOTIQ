// Robot profile and its action primitives: the UR5e arm, straight from the skill template (one source of truth).
import template from '../skills/templates/pick_place_ur5e.json';
import type { Device, OperationalLimits, Primitive, Robot } from '../skills/schema';

export const UR5E: Robot = { ...(template.robot as unknown as Robot), device: 'arm' };
export const ARM_PRIMITIVES = template.action_primitives as unknown as Primitive[];
export const ARM_LIMITS = UR5E.operational_limits as OperationalLimits;
export const ARM_HOME = UR5E.home_joints_deg as number[];

export const profiles: Record<Device, { robot: Robot; primitives: Primitive[] }> = {
  arm: { robot: UR5E, primitives: ARM_PRIMITIVES },
};

export function primitive(device: Device, id: string): Primitive | undefined {
  return profiles[device].primitives.find((p) => p.id === id);
}

// a param without a default that isn't marked optional must be given
export const isRequired = (spec: Primitive['parameters'][string]) => spec.default === undefined && !spec.optional;

// operational limits that cap a primitive's numeric params on top of its own min/max
export function limitFor(key: string, lim: OperationalLimits = ARM_LIMITS): { min?: number; max?: number } {
  switch (key) {
    case 'distance_m':
      return { min: 0, max: lim.max_step_m };
    case 'speed_mps':
      return { min: 0, max: lim.max_tcp_speed_mps };
    case 'speed_dps':
      return { min: 0, max: lim.max_joint_speed_dps };
    case 'angle_deg':
      return { min: -lim.max_rotation_step_deg, max: lim.max_rotation_step_deg };
    case 'height_m':
      return { min: lim.min_z_above_table_m, max: 0.5 };
    default:
      return {};
  }
}
