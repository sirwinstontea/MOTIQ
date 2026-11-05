# preview

`scene.ts` turns a skill's steps into an animation timeline with `buildTimeline`. It is pure TypeScript with no React Native imports, and [`SkillPreview`](../components/SkillPreview.tsx) renders it.

The arm is drawn from the side (x forward, z up) from the dry run in [`src/skills/dryrun.ts`](../skills/dryrun.ts). The preview is conceptual, not a physics simulation.
