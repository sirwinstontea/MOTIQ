// The MOTIQ skill format (schema 1.1.0). Mirrors src/skills/templates/pick_place_ur5e.json.
// Two small app extensions: robot.device ("arm", the UR5e) and a top-level "app" block (icon, builtin).

export type Device = 'arm';

export type ParamSpec = {
  type: 'number' | 'enum' | 'array' | 'object_id' | 'string';
  default?: unknown;
  min?: number;
  max?: number;
  values?: string[];
  items?: string;
  length?: number;
  optional?: boolean;
  note?: string;
};

export type Direction = 'forward' | 'backward' | 'left' | 'right' | 'up' | 'down';

export type Primitive = {
  id: string;
  voice_aliases_it?: string[];
  description: string;
  direction?: Direction;
  parameters: Record<string, ParamSpec>;
  postconditions?: string[];
  note?: string;
  urscript?: string;
};

export type Joint = {
  index: number;
  id: string;
  name_it?: string;
  motion?: string;
  range_deg: [number, number];
  max_speed_dps: number;
  note?: string;
};

export type OperationalLimits = {
  max_tcp_speed_mps: number;
  max_tcp_acceleration_mps2: number;
  max_joint_speed_dps: number;
  approach_speed_mps: number;
  min_z_above_table_m: number;
  safe_travel_height_m: number;
  default_step_m: number;
  max_step_m: number;
  default_rotation_step_deg: number;
  max_rotation_step_deg: number;
  note?: string;
};

export type Robot = {
  device?: Device;
  model: string;
  degrees_of_freedom?: number;
  payload_kg?: number;
  reach_m?: number;
  pose_repeatability_m?: number;
  max_tcp_speed_mps?: number;
  controller_language?: string;
  angle_units_in_skill?: string;
  joints?: Joint[];
  cartesian_dof?: Record<string, unknown>;
  direction_mapping?: {
    reference?: string;
    calibrated?: boolean;
  } & Partial<Record<Direction, { axis: 'x' | 'y' | 'z'; sign: 1 | -1 }>>;
  gripper?: {
    model: string;
    stroke_m: number;
    open_command_urscript?: string;
    close_command_urscript?: string;
    object_detection?: string;
  };
  home_joints_deg?: number[];
  operational_limits?: OperationalLimits;
};

export type Xyz = { x: number; y: number; z: number };

export type EnvObject = {
  id: string;
  name: string;
  category: string;
  attributes?: Record<string, unknown> & { size_m?: number; grip_width_m?: number };
  initial_state?: { location?: Xyz; orientation?: { type: string } };
  detection?: {
    required?: boolean;
    class?: string;
    reidentify_before_pick?: boolean;
    reidentify_before_place?: boolean;
  };
};

export type PrimitiveCall = { primitive: string; parameters?: Record<string, unknown> };

export type FailurePolicy =
  'STOP_AND_REOBSERVE' | 'RETRY_WITH_REOBSERVATION' | 'STOP_AND_REPLAN' | 'REPORT_FAILURE' | (string & {});

export type StepAction =
  | 'OVERVIEW'
  | 'DETECT'
  | 'PICK'
  | 'PLACE'
  | 'VISIT'
  | 'TOUCH'
  | 'PUSH'
  | 'VERIFY'
  | 'SEQUENCE'
  | (string & {});

export type SkillStep = {
  id: string;
  action: StepAction;
  target?: string;
  description?: string;
  parameters?: Record<string, unknown>;
  primitive_sequence?: PrimitiveCall[];
  preconditions?: string[];
  postconditions?: string[];
  success_conditions?: string[];
  failure_policy?: FailurePolicy;
  // run this step as one fixed bridge tool on the PHYSICAL arm instead of its primitives (sim runs the primitives)
  physical_tool?: 'ur_down_up' | 'ur_visit' | 'ur_go_to_pose';
};

export type SkillDoc = {
  schema_version: string;
  skill: {
    id: string;
    name: string;
    version: string;
    status: 'draft' | 'validated' | 'approved' | (string & {});
    description: string;
    created_at: string;
    updated_at: string;
    source?: Record<string, unknown>;
  };
  intent: { goal: string; natural_language_instruction: string; success_conditions: string[] };
  robot: Robot;
  action_primitives?: Primitive[];
  environment: {
    coordinate_system?: string;
    units?: string;
    workspace?: Record<string, unknown>;
    objects: EnvObject[];
  };
  constraints: {
    object_constraints?: Record<string, unknown>[];
    execution_constraints: Partial<{
      collision_avoidance: boolean;
      require_object_reidentification: boolean;
      require_workspace_validation: boolean;
      require_grasp_validation: boolean;
      allow_adaptive_replanning: boolean;
      open_gripper_before_descending: boolean;
      lift_before_horizontal_travel: boolean;
      respect_operational_limits: boolean;
    }>;
  };
  steps: SkillStep[];
  success_criteria?: Record<string, boolean>;
  interpretation?: {
    model?: string;
    status?: string;
    confidence?: number;
    human_review_required?: boolean;
    uncertainties?: string[];
  };
  safety: {
    physical_execution_allowed: boolean;
    requires_feasibility_check?: boolean;
    requires_operator_approval?: boolean;
    requires_robot_controller_limits?: boolean;
    // NEEDS_VALIDATION = refuse to move the real robot on unmeasured values; USE_SKILL_VALUES = trust the file
    unknown_parameters_policy?: 'NEEDS_VALIDATION' | 'USE_SKILL_VALUES' | (string & {});
    stop_primitive_always_available?: boolean;
    reject_moves_outside_operational_limits?: boolean;
  };
  review: { revision: number; approved: boolean; approved_by: string | null; approval_timestamp: string | null };
  app?: { icon?: string; builtin?: boolean };
};
