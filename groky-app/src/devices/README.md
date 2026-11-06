# devices

| File | Purpose |
|---|---|
| `tools.ts` | Device list, Grok tool definitions, `ARM_TASK_TOOLS`, `splitToolName` |
| `bridge.ts` | HTTP client for the bridge: `runTool`, `stopDevice`, `armSend`, `armPhysical`, `getArmSpeed`, `setArmSpeed`, `ping` |
| `sim.ts` | Fake robots for building the voice side without hardware. The arm is simulated in [`src/robot`](../robot). |
| `index.ts` | Re-exports |
