# vision/planner — Parte C: video → skill JSON → RoboDK

Questo modulo trasforma una dimostrazione video in una sequenza di chiamate a skill e la esegue su un UR5e simulato in RoboDK. Le skill che non esistono ancora vengono definite da Grok come composizione di primitive note. Il modulo non esegue mai codice generato.

Non dipende da `vision/eyes` né da `vision/grok`, e non usa OpenCV.

```
video ─ffmpeg─┬─ LQ 64×36 grigio (tutti i frame) ─► movimento ─► keyframe (inizio/picco/fine)
              ├─ HQ 720p (stesso indice/PTS) ◄────────── lookup ─┘
              └─ audio ─► Grok STT ─► frasi con timestamp ─► keyframe "voice" + contesto
                                │
             keyframe HQ + frasi ─► Grok vision ─► token d'azione (VideoTokens)
                                │
 skill_library.json ◄─ Grok: sintesi skill mancanti ◄─ Grok: piano (SkillPlan)
        │
        └─► espansione ─► validator (regole fisiche) ─► executor RoboDK/mock ─► run_<id>.json
```

## Installazione
```bash
cd computer-vision
python3 -m venv .venv && .venv/bin/pip install -r vision/planner/requirements.txt
cp .env.example .env   # XAI_API_KEY, XAI_BASE_URL, XAI_MODEL, XAI_STT_API_KEY (voce: solo chiave xAI)
```
ffmpeg viene preso dal sistema se è installato (`brew install ffmpeg`). Altrimenti si usa il binario incluso in `imageio-ffmpeg`.

## Uso
```bash
# Solo keyframe LQ→HQ (+ grafico debug motion.svg), senza Grok vision
python -m vision.planner index --video data/replays/sample_arm.mp4 --debug

# Video → token d'azione (Grok vision + STT)
python -m vision.planner tokenize --video demo.mp4 --out logs/tokens.json

# Pipeline completa, offline (MockGrok + executor in memoria)
python -m vision.planner --mock run --tokens tests/fixtures/planner/tokens_unscrew.json --executor mock

# Preparazione stazione RoboDK (RoboDK aperto con un UR5e caricato: File > Open online library > UR5e,
# oppure File > Open del file https://cdn.robodk.com/downloads-library/library-robots/UR5e.robot).
# Aggiunge la pinza "Gripper" (TCP 150 mm), crea gli oggetti della scena come scatole e porta il robot in home.
python -m vision.planner.robodk_setup [--scene tests/fixtures/planner/tokens_unscrew.json]

# Pipeline completa su RoboDK (RoboDK aperto con stazione UR5e + pinza + oggetti con nome)
python -m vision.planner run --video demo.mp4 --executor robodk [--robot UR5e] [--tool Gripper] \
    [--record-program Demo] [--collisions] [--replan-on-fail] [--require-approved]

# Libreria skill
python -m vision.planner skills                    # elenco
python -m vision.planner skills --show svitare     # dettaglio JSON
python -m vision.planner skills --approve svitare  # needs_validation -> approved
```

## I due JSON vivi
| File | Contenuto |
|---|---|
| `data/skills/skill_library.json` | Libreria persistente. Contiene le skill builtin (`home`, `pick`, `place`, `pick_and_place`) e quelle apprese, con `status: needs_validation`, `version` e una `history` degli eventi. |
| `logs/runs/run_<id>.json` (+ `latest.json`) | Stato della sessione: token, piano, skill apprese, step `pending/running/done/failed/skipped` con il feedback del robot, ed eventi. Viene riscritto in modo atomico a ogni transizione, così si può leggere in polling. |

Una skill è una lista `body` di elementi, ognuno dei quali è uno tra:
- `{"primitive": "MOVE_DOWN", "args": {"distance_m": "$depth"}}`;
- `{"skill": "pick", "args": {"target": "$obj"}}`;
- `{"repeat": "$turns", "body": [...]}`.

Le primitive sono quelle del catalogo chiuso in `skill_pick_place_ur5e.json`.

## Sicurezza
- **Validatore:** controlla la sequenza prima di eseguirla. Fa rispettare:
  - catalogo chiuso delle primitive e parametri entro min/max;
  - `z` sopra `min_z_above_table_m`;
  - pinza aperta prima di scendere;
  - salita prima di traslare;
  - velocità entro i limiti operativi;
  - sbraccio.

  Gli errori tornano a Grok, che ha al massimo 3 iterazioni di piano e 3 tentativi di sintesi per skill.
- **RoboDK:** gira sempre in `RUNMODE_SIMULATE`. Prima di ogni `MoveL` c'è un controllo IK (`SolveIK`); con `--collisions` si aggiunge `MoveL_Test`. Il robot reale non viene mai comandato.
- **Skill apprese:** nascono `needs_validation`. Con `--require-approved` il run si blocca se il piano le usa.

## Test
```bash
GROK_MOCK=1 .venv/bin/python -m pytest tests/test_planner.py -v
```
