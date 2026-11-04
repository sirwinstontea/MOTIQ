# Visione → App: come collegarsi

Tutto quello che vede la visione esce da un server HTTP sul PC con la camera.
CORS è aperto, quindi l'app può chiamarlo da qualsiasi origine.

## 1. Avvio (lo facciamo noi, sul PC)

```bat
run_server.bat
```
(= `.venv\Scripts\python.exe -m vision.eyes.cli run --port 8000`)

- Camera: la RealSense collegata in USB-C (colore + profondità) se c'è, se no la webcam del portatile.
  Per forzarla: `--mode realsense`, `--mode webcam --device 0`; `--mode synthetic` = scena finta, senza camera.
- Solo la sorgente `arm`; `dog` si aggiunge con `--dog-target <indice o stream>`.
- Indirizzo base: `http://<IP-del-PC>:8000` (l'IP lo trovate con `ipconfig`, stessa Wi-Fi). Se dal telefono
  non risponde, è il firewall di Windows: va permesso Python sulla rete privata.
- Prova veloce: `GET /health` → `{"ok": true, "sources": ["arm"]}`.
- Documentazione interattiva generata dal server: `http://<IP-del-PC>:8000/docs`.
- Il server e `run_demo.bat` non possono usare la stessa camera contemporaneamente.

## 2. Endpoint

`source` è sempre `"arm"` (camera del braccio) o `"dog"` (cane robot).

| Metodo | Percorso | Cosa fa |
|---|---|---|
| `GET` | `/health` | Server vivo e sorgenti attive |
| `GET` | `/stream/{source}` | Video MJPEG con riquadri e numeri. Basta `<img src>` |
| `GET` | `/stream/{source}/raw` | Video MJPEG pulito |
| `GET` | `/frame/{source}.jpg?raw=false` | Un solo fotogramma JPEG |
| `WS` | `/ws/objects/{source}?hz=15` | `FramePayload` continuo (default 15 al secondo) |
| `GET` | `/state/{source}` | Un solo `FramePayload` |
| `POST` | `/dog/target` | `{target_id?, target_label?}` → il cane insegue quell'oggetto |
| `POST` | `/action/{source}/start` | `{action, target_id, destination_id?}`: segna lo stato "prima" dell'azione del robot |
| `POST` | `/action/{source}/verify_local` | Confronta prima/dopo: l'oggetto è sparito / arrivato? |
| `GET` | `/locate/{source}?label=...&n=10` | Posizione di un oggetto nel frame **base del robot** (camera sul braccio, vedi §7) |
| `GET` | `/voice/tools` | Strumenti da passare a Grok Voice (`session.tools`) |
| `POST` | `/voice/dispatch` | `{name, arguments}` → esegue lo strumento chiesto da Grok Voice |

## 3. Tipi TypeScript

```ts
export type Source = "arm" | "dog";

/** [x, y, w, h] normalizzati 0..1, (x, y) = angolo in alto a sinistra. Moltiplicate per la larghezza/altezza del video. */
export type BBox = [number, number, number, number];

export interface VisionObject {
  id: number;               // numero mostrato sul video; può cambiare se il tracker perde l'oggetto
  label: string;            // classe, es. "cup"
  name: string | null;      // nome breve dato da Grok, se c'è
  conf: number;             // 0..1
  bbox: BBox;
  color: string | null;     // in italiano, es. "rosso"
  coords_mm: { x: number; y: number; angle_deg: number } | null; // null finché il braccio non è calibrato
  distance_m: number | null; // stima, non misura
  // Camera sul braccio (§7): null senza profondità, senza calibrazione hand-eye o con il robot in moto
  xyz_cam_m: [number, number, number] | null;   // frame camera, m
  xyz_base_m: [number, number, number] | null;  // frame base del robot, m
  yaw_base_deg: number | null;                  // asse lungo attorno a Z base, [-90, 90]; null se rotondo
}

export interface DogTelemetry {
  target_id: number | null;
  target_visible: boolean;
  error_x: number;     // -1 sinistra .. 0 centro .. +1 destra
  error_y: number;     // -1 alto .. +1 basso
  bbox_height: number; // 0..1, più grande = più vicino
}

export interface FramePayload {
  source: Source;
  frame_id: number;
  ts: number;          // secondi Unix
  objects: VisionObject[];
  dog_telemetry: DogTelemetry | null; // solo per "dog" con un target impostato
  safety_stop: boolean;
  safety_reason: string | null;
}

export interface VerifyResult {
  ok: boolean;
  success: boolean;
  disappeared_from_source: boolean;
  arrived_at_destination: boolean | null;
  source_iou: number;
  destination_iou: number;
  reason: string;
}

/** Risposta di /voice/dispatch: c'è sempre `say`, la frase che la voce deve dire. */
export interface VoiceResult {
  ok: boolean;
  say: string;
  error?: string;
  // list_objects:
  objects?: { id: number; name: string }[];
  // select_object:
  id?: number | null;
  action?: string;               // "pick" | "place" | "none" ...
  destination_id?: number | null;
  ambiguous?: boolean;           // true = `say` è una domanda da fare all'utente
  // verify_last_action:
  success?: boolean;
}
```

## 4. Esempi

```ts
const BASE = "http://192.168.1.50:8000"; // IP del PC

// Video in groky-app: nessun codice, basta il .env (cameras.ts legge un JPEG alla volta)
//   EXPO_PUBLIC_CAMERAS=Visione=http://192.168.1.50:8000/frame/arm.jpg
// (?raw=true per il frame senza riquadri. /stream/arm è MJPEG: va bene solo in un browser, non in <Image> di React Native)

// Oggetti dal vivo
const ws = new WebSocket(`${BASE.replace("http", "ws")}/ws/objects/arm?hz=10`);
ws.onmessage = (e) => {
  const frame: FramePayload = JSON.parse(e.data);
  // frame.objects -> lista / overlay cliccabile (bbox * dimensioni dell'<img>)
};

// Il cane insegue l'oggetto 3
await fetch(`${BASE}/dog/target`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ target_id: 3 }),
});
```

### Grok Voice (realtime)

L'app apre la sessione vocale con xAI; la visione risponde agli strumenti.

1. `const tools = await (await fetch(`${BASE}/voice/tools`)).json()` → mandateli in `session.update` → `session.tools`.
2. A ogni evento `response.function_call_arguments.done`:
   ```ts
   const result: VoiceResult = await (await fetch(`${BASE}/voice/dispatch`, {
     method: "POST", headers: { "Content-Type": "application/json" },
     body: JSON.stringify({ name: ev.name, arguments: ev.arguments }), // arguments può restare la stringa JSON
   })).json();
   send({ type: "conversation.item.create",
          item: { type: "function_call_output", call_id: ev.call_id, output: JSON.stringify(result) } });
   ```
3. Dopo aver risposto a **tutte** le chiamate di quel turno: `send({ type: "response.create" })`.

Strumenti: `list_objects`, `select_object` (con `description` = le parole dell'utente), `describe_scene`,
`verify_last_action`. Se `ambiguous` è `true`, la voce fa la domanda in `say` e la risposta dell'utente
va in un nuovo `select_object`: il contesto lo teniamo noi. Quando `select_object` riesce, la scelta va
anche al braccio in `logs/selection.json` (come il click nel demo): `sent_to_robot` dice se è stata scritta.

#### Cosa cambiare in groky-app (due file, nient'altro)

`src/lib/config.ts`: un campo nuovo, **diverso** da `visionUrl` (quello è il modello vision di xAI):
```ts
  visionApi: (process.env.EXPO_PUBLIC_VISION_API ?? '').replace(/\/$/, ''), // es. http://192.168.1.50:8000
```

`src/voice/session.ts`: i nostri strumenti con il prefisso `vision_`, per non scontrarsi con quelli del bridge.
```ts
// in cima alla classe
private visionTools: any[] = [];

// all'apertura della sessione, prima del primo session.update (vuoto se il server non risponde: l'app va come oggi)
async loadVisionTools() {
  if (!env.visionApi) return;
  try {
    const tools = await (await fetch(`${env.visionApi}/voice/tools`)).json();
    this.visionTools = tools.map((t: any) => ({ ...t, name: `vision_${t.name}` }));
  } catch {
    this.visionTools = [];
  }
}

// tools(): aggiungere ...this.visionTools
return [...grokTools(), lookTool, ...this.visionTools, ...(list.length ? [skillTool(list)] : [])];

// call(): un ramo prima dell'else finale che chiama runTool
} else if (name.startsWith('vision_')) {
  res = await (await fetch(`${env.visionApi}/voice/dispatch`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name.slice('vision_'.length), arguments: args }),
  })).json();
  this.trace({ id, kind: 'vision', title: String(res.say ?? name), ok: res.ok, ms: Date.now() - t0 });
}
```
`.env` dell'app: `EXPO_PUBLIC_VISION_API=http://<IP-del-PC>:8000`. Grok legge il campo `say` del risultato e lo dice.

`XAI_API_KEY` non va nel codice dell'app (finirebbe nel bundle): ditecelo se vi serve un modo per ottenerla dal server.

## 5. Esempi reali (scena di prova)

```json
// GET /state/arm
{"source": "arm", "frame_id": 3, "ts": 1793787986.08,
 "objects": [{"id": 1, "label": "box", "name": null, "conf": 0.87, "bbox": [0.7188, 0.5208, 0.1266, 0.1479],
              "color": "verde", "coords_mm": null, "distance_m": 1.2}],
 "dog_telemetry": null, "safety_stop": false, "safety_reason": null}

// POST /voice/dispatch {"name": "select_object", "arguments": {"source": "arm", "description": "il numero 1"}}
{"source": "local", "latency_ms": 0.6, "ok": true, "id": 1, "destination_id": null, "action": "none",
 "say": "Ho selezionato box verde."}

// GET /state/dog (dopo POST /dog/target {"target_id": 1})
{"dog_telemetry": {"target_id": 1, "target_visible": true, "error_x": 0.5642, "error_y": 0.1895, "bbox_height": 0.1479}}
```

## 6. Cosa ancora non c'è

- `select_object` restituisce l'`id` ma **non muove il braccio**: oggi la scelta arriva al braccio
  (`logs/selection.json`) solo dalla demo sul PC. Da decidere con il gruppo robotica chi la manda.
  Il vostro `arm_pick_place` passa il nome dell'oggetto ("red cube"), noi diamo un `id`: da allineare.
- Il tasto G della demo (Grok trova oggetti che YOLO non conosce) non è esposto dal server.
- `coords_mm` resta `null` finché non si calibra il braccio con i marker ArUco. Con la camera sul braccio
  (`hand_eye.json` presente) l'omografia fissa non si usa più: le coordinate per il robot sono `xyz_base_m` (§7).

## 7. Camera sul braccio: posizione dell'oggetto per il robot

La RealSense è sul polso dell'UR5e. Per ogni oggetto la visione trova prima il punto nel frame **camera** (`xyz_cam_m`),
poi lo porta nel frame **base** del robot (`xyz_base_m`), cioè nelle coordinate che il robot usa per muoversi.

- `P_cam`: si prende il centro della maschera dell'oggetto. La profondità è la mediana della D435 sulla maschera, e con
  gli intrinseci della camera il pixel diventa un punto in metri.

### Modo consigliato: posa fissa

Il braccio guarda il tavolo **sempre dalla stessa posa** (la "posa di osservazione"). Così camera → base è una sola
matrice che non cambia mai, e non serve leggere la posa del robot:

```
P_base = T_base_cam · P_cam
```

Calibrazione (una volta, oppure ogni volta che cambiate la posa di osservazione o toccate il supporto della camera):

1. Stampate il foglio (`calibrate-arm --generate-board`, al 100%) e fissatelo piatto sul tavolo, sotto la camera.
2. Toccate con la punta del TCP il centro dei 4 marker e scrivete X e Y (mm, frame base, come sul pendant) in
   `data/calibration/markers_mm.json`: `{"0": [x, y], "1": [x, y], "2": [x, y], "3": [x, y]}`. Annotate anche la Z
   del tavolo.
3. Portate il braccio nella posa di osservazione (circa 40 cm sopra il tavolo, camera verso il basso) e salvatela sul pendant.
4. Lanciate `python -m vision.eyes.cli calibrate-look-pose --table-z-mm <Z del tavolo>`. Il comando stampa:
   - l'errore di riproiezione: bene se < 1 px;
   - il **controllo con la profondità**: il tavolo visto dalla D435 deve risultare alla Z attesa, entro ±5 mm;
   - un'immagine `logs/look_pose_check.jpg` con gli assi del base disegnati.

   Il risultato va in `data/calibration/fixed_pose.json`.
5. Per sapere se il braccio è tornato nella stessa posa o se la camera si è spostata:
   `calibrate-look-pose --check`, che confronta e non salva niente.
6. Registrate lo sfondo del "tavolo vuoto" dalla posa di osservazione: lì le maschere dalla profondità funzionano meglio.

`run` carica da solo `fixed_pose.json` se c'è. Da quel momento `xyz_base_m` è valido **solo con il braccio nella posa di
osservazione**: il controller ci va, chiama `/locate`, poi scende.

### Modo alternativo: hand-eye (pose qualsiasi)

```
P_base = T_base_tool (posa TCP allo scatto) · T_tool_cam (calibrazione hand-eye) · P_cam
```

Serve solo se un giorno volete misurare da pose diverse. Ha la precedenza la posa fissa, se il suo file esiste.

- `T_tool_cam`: si calibra una volta sola con `python -m vision.eyes.cli calibrate-hand-eye`. Si usa il foglio A4 ArUco
  (`calibrate-arm --generate-board`) fermo sul tavolo e 12-15 pose del braccio ruotate su almeno 2 assi. Il risultato va in
  `data/calibration/hand_eye.json`, con la dispersione in mm (bene se < 5).
- `T_base_tool`: posa TCP letta in **sola lettura** da robot_controller `GET /robot/state`. Per provare senza
  controller c'è `run --look-pose "x,y,z,rx,ry,rz"` (mm e rad, come sul pendant). Calibrazione e runtime devono usare
  la stessa fonte del TCP.
- Si pubblica **solo a robot fermo**: la posa letta prima e dopo il frame deve coincidere (1 mm / 0,5°) e `busy` deve
  essere `false`.

### `GET /locate/arm?label=vite_arancione&n=10&timeout_s=3` (oppure `id=3`)

Aspetta `n` frame nuovi (con `n=1` basta una sola cattura, come nella specifica del collega) e restituisce la mediana.
Il formato segue il contratto della specifica (§8):

```json
{"ok": true, "status": "ok", "timestamp_utc": "2026-11-04T18:30:00.000Z", "camera": "RealSense D435",
 "object_class": "vite_arancione", "point_semantics": "visible_surface_centroid_3d",
 "position_camera_m": [0.072, -0.018, 0.463], "position_base_m": [-0.452, -0.198, 0.031],
 "yaw_base_deg": 12.5, "valid_depth_ratio": 0.93, "num_valid_points": 2347, "spread_m": 0.0021, "n": 10,
 "calibration_id": "fixed_pose_20261104-1830", "frame_id": "base", "id": 3, "tcp_pose": null}
```

- `point_semantics = visible_surface_centroid_3d`: è il **centroide 3D della superficie visibile**, cioè la mediana di
  tutti i punti 3D validi della maschera. Non è il centro geometrico dell'oggetto: guardando dall'alto è il centro della
  faccia superiore.
- `xyz_base_m` resta come sinonimo di `position_base_m`.

Se qualcosa non va, la risposta è `ok: false`, `position_base_m: null` e uno `status` esplicito, mai coordinate che
sembrano valide:

| `status` | Significato |
|---|---|
| `missing_calibration` | Niente `fixed_pose.json` o `hand_eye.json` |
| `object_not_detected` | L'oggetto non si vede |
| `object_partially_visible` | Tagliato dal bordo dell'immagine: il centroide sarebbe sbagliato |
| `invalid_depth` | Visto, ma senza profondità valida (superficie nera, lucida, troppo vicina) |
| `unsynced_robot_pose` | Hand-eye: robot in moto o posa del robot non disponibile |
| `unstable_measurement` | Misure più sparse di `max_spread_m` (1 cm di default) |
| `insufficient_frames` | Meno di `n` frame validi entro `timeout_s` |

### Come scegliere la posa di osservazione

1. In alto, abbastanza da vedere tutta la zona di lavoro. A 40 cm la D435 inquadra circa 45 × 34 cm, a 50 cm circa
   56 × 42 cm. Più in alto la profondità diventa più rumorosa: tra 35 e 50 cm va bene.
2. Camera **verso il basso**, quasi perpendicolare al tavolo: le misure di XY e lo yaw sono più precise.
3. Vite e secchio **lontani dai bordi** dell'immagine: un oggetto tagliato dà `object_partially_visible`.
4. Il braccio non deve fare ombra né entrare nell'inquadratura.

Dopo la calibrazione, `calibrate-look-pose` stampa il rettangolo di tavolo inquadrato (X e Y in mm nel base). In RoboDK
lo stampa `robodk-check` per la posa in cui si trova il robot, e dice se ogni oggetto è dentro o fuori.

### Simulazione in RoboDK (senza robot vero, sola lettura)

Serve RoboDK 5.4.3 o successivo avviato con la stazione UR5e. Il pacchetto Python `robodk` c'è già nell'installazione
di RoboDK: basta `set PYTHONPATH=C:\RoboDK\Python` prima dei comandi (oppure `pip install robodk`). La visione **non
muove mai il robot**: lo si sposta a mano nella finestra di RoboDK. La camera simulata è una D435 (640×480, campo
verticale 42°) montata sulla flangia con `--robodk-mount "x,y,z,rx,ry,rz"` (mm, rad; di default 7 cm di lato e 3 cm
avanti). Visto che YOLO non riconosce gli oggetti renderizzati, gli oggetti si riconoscono per colore con
`--color-labels "arancione=vite_arancione,blu=secchio_blu"`.

1. Nella stazione aggiungete una vite arancione e un secchio blu sul tavolo, con lo stesso nome dell'etichetta
   (`vite_arancione`, `secchio_blu`).
2. Spostate il robot in una posa di osservazione candidata (nella finestra di RoboDK) e lanciate
   `python -m vision.eyes.cli robodk-check`. Stampa dove si trova la camera, il rettangolo di tavolo inquadrato e se
   vite e secchio sono dentro. Ripetete finché la posa va bene: è il modo più rapido per **definire la posa di
   osservazione** prima di provarla sul braccio vero.
3. `robodk-check --save-truth` salva come calibrazione la posa vera della camera, che RoboDK conosce. Così si prova la
   catena profondità → base anche senza il foglio ArUco nella scena.
4. `robodk-check --record-empty` registra il tavolo vuoto, nascondendo un attimo gli oggetti, poi confronta ogni
   `/locate` con la posizione vera di RoboDK (errore XY in mm; la Z di RoboDK è l'origine del CAD, non la cima). Bene
   se l'errore XY sta sotto i 5 mm. Senza tavolo vuoto le maschere usano GrabCut, che sugli oggetti grandi e uniformi
   (il secchio) può fallire con `invalid_depth`: registrate sempre il tavolo vuoto dalla posa di osservazione.
5. Prova completa della calibrazione: mettete nella scena il foglio ArUco come immagine A4 sul tavolo e lanciate
   `calibrate-look-pose --camera robodk`, con i centri dei marker nel base in `markers_mm.json`.
6. Profondità di RoboDK verificata sulla 6.2: arriva via socket, è già la Z del piano e non è invertita. Se con un'altra
   versione il controllo del tavolo stampato da `calibrate-look-pose` è sbagliato di molto, provate
   `--robodk-depth-invert` e/o `--robodk-depth-ray`.

**Risultato della prova del 4/11/2026 (RoboDK 6.2, UR5e, camera a 52 cm sopra il tavolo):** calibrazione dal foglio
ArUco con la camera stimata a meno di 1 mm da quella vera, riproiezione 0,81 px, tavolo dalla profondità a −0,1 mm.
Vite: errore XY 2,6 mm. Secchio: 0,2 mm. Nella prima posa provata (flangia a 45 cm) la vite era fuori inquadratura e
il secchio, alto 11,5 cm, veniva tagliato dal bordo (`object_partially_visible`). Per un oggetto alto serve una posa più
alta o più centrata.

Anche la hand-eye si prova in simulazione:
`calibrate-hand-eye --camera robodk --tcp robodk`. La posa della flangia arriva da `SolveFK(Joints())`, senza TCP come
chiede la specifica, e la `T_F_C` trovata si confronta con `--robodk-mount`.

### Cosa serve dalla parte del robot (robot_controller / bridge, non lo tocchiamo noi)

1. **Look pose**: prima del pick, il braccio va in una posa insegnata a circa 0,40 m sopra il tavolo con la camera verso il
   basso, si ferma e chiama `/locate`. Sotto i 15-30 cm la D435 non misura: la discesa finale è cieca.
2. **Usare la posa**: oggi `SafetyPolicy.check_detection` rifiuta ogni detection con una posa
   (`robot_control/safety/policy.py`). Va accettata solo se `frame_id == "base"`, con XY dentro una zona di presa
   consentita e `spread_m` piccolo.
3. **Pick su XY + yaw da visione, Z dai target insegnati**: `xyz_base_m[2]` è la superficie dell'oggetto e su
   anello e secchio può cadere sul tavolo. Va usata solo come controllo (± qualche cm rispetto a tavolo + altezza
   dell'oggetto). Orientamento: pinza verso il basso (`[π, 0, 0]`) ruotata di `yaw_base_deg` attorno a Z, più l'offset
   di montaggio della pinza.
4. **Bridge**: con la camera sul polso, dalla home l'oggetto può non vedersi. Il bridge non deve rifiutare il pick per
   "non visibile" prima di essere andato nella look pose.
