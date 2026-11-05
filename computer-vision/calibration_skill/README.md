> **DEPRECATO / DEPRECATED (2026-11-05).** Questa skill muove il braccio da sola e usa la posa del **TCP attivo** come
> se fosse la flangia: entrambe le cose sono vietate da `MOTIQ_Codex_Calibration_Skill_EN.md`. Non importatela e non
> approvatela per il braccio vero. Usate **Skills → Calibration** nell'app (wizard senza movimenti, flangia verificata,
> scacchiera 21,8 mm): vedi `computer-vision/vision/eyes/README.md`, sezione *Skills → Calibration*.
> The `/calibration/arm/{reset,sample,solve,look_pose}` endpoints below remain only for backward compatibility.

# Skill "Calibrate arm camera" per Aman

Calibra la RealSense sul polso dell'UR dall'app, **senza toccare il foglio con la punta**. La calibrazione la fai tu sul
braccio vero: qui c'è tutto quello che serve. La visione **non muove mai il robot**: si muove solo l'app, con i
suoi passi piccoli (bridge → `ur_jog`), e a ogni posa chiede alla visione di salvare un campione.

## Cosa c'è

| File | A cosa serve |
|---|---|
| `skill_calibra_camera_braccio.json` | la skill (schema 1.1.0, 27 passi, 12 pose). Si importa dalla scheda Skills → *Import skill* → incolla il JSON |
| `make_skill.py` | la rigenera: cambia `POSES` per altre pose. Controlla da solo passi ≤ 10 cm e giunti ≤ 20° |

La visione (già pronta, in `computer-vision/`) espone quattro POST. Il corpo può essere vuoto.

| Chiamata | Cosa fa |
|---|---|
| `POST /calibration/arm/reset` | azzera i campioni |
| `POST /calibration/arm/sample` | foglio visto ora + posa del braccio (letta via RTDE, sola lettura) → un campione. `ok:false` se il braccio si muove, il foglio non si vede o la posa è uguale alla precedente |
| `POST /calibration/arm/solve` | hand-eye → `data/calibration/hand_eye.json` e **`markers_mm.json` con i centri dei marker nel frame Base** (niente da toccare). `ok:false` se meno di 3 campioni o dispersione > 5 mm |
| `POST /calibration/arm/look_pose` | dalla posa di osservazione, col foglio ancora lì → `fixed_pose.json`, attivata subito senza riavvio |

## 1. Modifiche all'app (due file, ~25 righe)

**`groky-app/src/skills/schema.ts`** — allarga il tipo:

```ts
physical_tool?: 'ur_down_up' | 'calibrate_reset' | 'calibrate_sample' | 'calibrate_solve' | 'calibrate_look_pose';
```

**`groky-app/src/skills/runner.ts`** — importa `env` (`import { env } from '../lib/config';`), aggiungi sopra `runSkill`:

```ts
const CALIBRATION = {
  calibrate_reset: 'reset', calibrate_sample: 'sample', calibrate_solve: 'solve', calibrate_look_pose: 'look_pose',
} as const;

// the vision server never moves the arm: it only saves a sample / solves / writes the calibration files
async function visionCalibrate(tool: keyof typeof CALIBRATION): Promise<Res> {
  if (!env.visionServer) return { ok: false, error: 'EXPO_PUBLIC_VISION_SERVER is not set' };
  try {
    const res = await fetch(`${env.visionServer}/calibration/arm/${CALIBRATION[tool]}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    });
    const d = await res.json().catch(() => ({}));
    const note = tool === 'calibrate_sample' && d.ok ? `sample ${d.n}` : tool === 'calibrate_solve' && d.ok
      ? `spread ${d.spread_mm} mm` : undefined;
    return { ok: !!d.ok, note, error: d.ok ? undefined : String(d.reason ?? `HTTP ${res.status}`) };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
```

e dentro `once`, **prima** del blocco `ur_down_up`:

```ts
    if (device === 'arm' && real && step.physical_tool?.startsWith('calibrate_')) {
      const r = await visionCalibrate(step.physical_tool as keyof typeof CALIBRATION);
      // a missed sample (sheet out of view for one pose) is not fatal: solve checks there are enough
      if (!r.ok && step.physical_tool === 'calibrate_sample') return { ok: true, note: `sample skipped: ${r.error}` };
      return r;
    }
```

`index.ts` non serve toccarlo: i passi `calibrate_*` hanno un `WAIT`, ammesso sul braccio vero, quindi la validazione passa.

## 2. Sul PC con la camera

```bash
.venv\Scripts\python.exe -m pip install ur_rtde
```
```bash
run_server.bat --robot-ip <IP dell'UR>
```

`EXPO_PUBLIC_VISION_SERVER=http://<IP-del-PC>:8000` nell'`.env` dell'app (c'è già se usi le skill con la camera).
Con `--robot-ip` la visione legge la posa dell'UR in **sola lettura** (RTDE `RTDEReceiveInterface`, che non può
muovere il robot).

## 3. Prima di premere Play

1. Foglio ArUco stampato **al 100%** (quadrato nero di lato 50,8 mm), **piatto e fermo** sul tavolo per tutta la prova.
2. Porta il braccio dal pendant nella **posa di osservazione** (camera verso il basso, flangia circa 50-55 cm sopra il
   tavolo, foglio al centro dell'immagine: guarda `http://<IP-del-PC>:8000/stream/arm`). È anche il punto di
   partenza della skill e quello a cui tornerà.
3. Chiudi RealSense Viewer e `run_demo.bat`.
4. Scheda Skills → importa il JSON → **approvala** per l'esecuzione fisica (come `ur_down_up`). Serve almeno un'occhiata
   alle mosse: sono 12 pose di soli passi piccoli (≤ 6 cm; polsi ≤ 20° alla volta).
5. Una mano sullo **STOP**: lo STOP dell'app ferma il braccio come per le altre skill.

## 4. Cosa succede, e cosa guardare

- Passi 2-24: per ogni posa il braccio fa una mossa piccola e poi `calibrate_sample`. L'app mostra `sample 1 … sample 12`.
  Se un campione salta ("foglio non visto") la skill va avanti. Servono almeno 3 campioni, meglio 10-12.
- Passo 26 (`calibrate_solve`): deve dare **`spread` sotto 5 mm** ("dispersione"). Altrimenti la visione risponde
  `ok:false`. Cause tipiche: foglio non piatto o spostato, supporto della camera che si muove, ritardo tra
  posa e immagine.
- Passo 27 (`calibrate_look_pose`): il braccio è tornato nella posa di partenza. Controlla nella risposta: errore di
  riproiezione sotto 1 px e **`table_check_mm` entro ±5**. Scrive `fixed_pose.json`.
- Alla fine: togli il foglio e registra il tavolo vuoto:
  ```bash
  curl.exe -X POST http://localhost:8000/empty_table/arm
  ```

## 5. Se qualcosa non va

| Sintomo | Probabile causa |
|---|---|
| Quasi tutti i campioni "foglio non visto" | posa di osservazione troppo bassa o foglio non al centro: le inclinazioni di 10° spostano l'immagine di circa 8 cm |
| `posa del braccio non disponibile` | il server non è partito con `--robot-ip`, o `ur_rtde` non è installato |
| `il braccio si sta muovendo` | il campione è partito troppo presto: aumenta il `WAIT` prima di `calibrate_sample` in `make_skill.py` |
| `solve` con dispersione alta | rifai con il foglio ben piatto; più pose con rotazioni diverse |
| `look_pose` con `markers_mm.json ha ancora le posizioni sul foglio` | `calibrate_solve` non è riuscito: i centri nel Base non sono stati scritti |

**Nota sui polsi:** i giunti `wrist_1` e `wrist_2` inclinano la camera su due assi, `wrist_3` la ruota. Se sul tuo UR
l'effetto è diverso (dipende dalla posa), cambia i numeri in `POSES` e rigenera. Basta che le 12 pose abbiano
rotazioni attorno ad almeno due assi diversi e il foglio sempre in vista.

Il TCP attivo sul pendant non cambia l'esito della posa fissa; deve però restare lo stesso per tutta la prova.
