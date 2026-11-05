# robot

The app's own model of the arm, used to check a primitive before it is sent anywhere.

| File | Purpose |
|---|---|
| `kinematics.ts` | UR5e forward kinematics from the official DH table and damped least-squares IK. Pure TypeScript. |
| `arm.ts` | `applyArm`: checks a primitive against operational limits, joint ranges, reach and the base singularity, then predicts arm and object state. The same function drives the simulator. |
| `profiles.ts` | Robot profiles. The arm comes from the skill template, the dog is a small hand-written profile. |
| `runtime.ts` | Live arm and world state. `runArm` checks, then simulates or sends to the bridge. Also `detect` and `verify`. |
