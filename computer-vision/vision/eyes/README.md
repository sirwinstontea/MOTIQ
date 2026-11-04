# Visione A ("Occhi"): Percezione Locale

Modulo di percezione veloce, interamente locale, per il **Grok Hackathon at the Museum** (Pavia, 4–5 Novembre 2026).

Si occupa di:
1. **Acquisizione sorgenti** (`vision/eyes/capture/`): webcam del braccio, stream HTTP/RTSP del cane/telefono, modalità `replay` da video registrati (`.mp4`) e modalità `synthetic` per test senza telecamera.
2. **YOLO + Tracciamento ByteTrack** (`vision/eyes/tracking/`): ID stabili nel tempo, classi YOLO, `bbox` normalizzati `[x, y, w, h]` in `[0.0, 1.0]` e stima locale del colore dominante in italiano (`rosso`, `blu`, `verde`, `giallo`, ...) per abilitare istantaneamente la scorciatoia locale di Visione B (`vision/grok/local_resolver.py`). Include anche un tracker leggero OpenCV (`--mock-tracker` / `EYES_MOCK=1`) per lavorare su qualsiasi PC senza GPU.
3. **Set-of-Mark** (`vision/eyes/marks/`): disegna i riquadri e i badge numerici ad alto contrasto coerenti con `vision/grok/marks.py` senza alterare il frame originale.
4. **Calibrazione Braccio** (`vision/eyes/calibration/`): rilevamento 4 marker ArUco (`DICT_4X4_50`), calcolo omografia 2D pixel $\to$ millimetri (`X_mm, Y_mm`), salvataggio su `calibration_arm.json` e stima dell'angolo di presa (`angle_deg` in `[-90°, +90°]`).
5. **Telemetria Cane & Safety Stop** (`vision/eyes/dog/`): calcola `target_visible`, errore orizzontale `error_x` in `[-1.0, +1.0]`, altezza del riquadro `bbox_height` e attiva lo stop di sicurezza immediato (`safety_stop=True`) se una persona è troppo vicina.
6. **Stato Condiviso `LiveVisionState`** (`vision/eyes/state/`): implementa l'interfaccia astratta `VisionState` di `vision/grok/voice_tools.py` in modo thread-safe.
7. **Verifica Oggettiva Locale** (`vision/eyes/verification/`): controlla in modo deterministico se l'oggetto è sparito dalla zona di partenza (`check_disappearance`) ed è arrivato nella destinazione (`check_appearance`).
8. **Server Streaming MJPEG + WebSocket** (`vision/eyes/server/`): espone il video MJPEG Set-of-Mark e il WebSocket con il JSON degli oggetti per l'App mobile e la Robotica.

---

## 🚀 Avvio Rapido e CLI

Tutti i comandi si lanciano dalla cartella radice del progetto (`Grok_Hackaton`):

### 1. Generare tavola ArUco e calibrare il braccio
```bash
# 1. Stampa il foglio A4 (al 100%) con i marker 0-3 e appoggialo sul tavolo
python -m vision.eyes.cli calibrate-arm --generate-board logs/aruco_a4.png

# 2. Misura dove sono i centri dei marker nel sistema del braccio (mm) e scrivili in
#    data/calibration/markers_mm.json

# 3. Calibra dalla camera (RealSense USB-C se c'è, se no webcam 0) -> data/calibration/calibration_arm.json
python -m vision.eyes.cli calibrate-arm
#    oppure, a demo aperto, inquadra i 4 marker e premi [A]
```
La calibrazione vale per la risoluzione con cui è fatta (es. 640x480 in USB 2, 848x480 in USB 3):
se cambia, le coordinate in mm si spengono finché non ricalibri. Controllo visivo: `logs/calibration_check.jpg`.

### 2. Generare un video MP4 per la modalità Replay
```bash
python -m vision.eyes.cli generate-replay --output data/replays/sample_arm.mp4
```

### 3. Ispezionare un singolo frame e stampare il JSON
```bash
python -m vision.eyes.cli inspect --source arm --mode replay --file data/replays/sample_arm.mp4 --calibration data/calibration/calibration_arm.json --save-marked data/sample_marked.jpg
```

### 4. Avviare il Server Live (MJPEG + WebSocket)
```bash
# In modalità sintetica o replay (senza hardware collegato)
python -m vision.eyes.cli run --mode replay --file data/replays/sample_arm.mp4 --port 8000

# Dal vivo con webcam USB sul braccio
python -m vision.eyes.cli run --mode webcam --device 0 --port 8000
```

---

## 🌐 Endpoint del Server (`http://localhost:8000`)

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| `GET` | `/health` | Stato del server e sorgenti attive (`arm`, `dog`) |
| `GET` | `/stream/{source}` | Stream video **MJPEG** con riquadri e numeri **Set-of-Mark** |
| `GET` | `/stream/{source}/raw` | Stream video **MJPEG** del frame originale pulito |
| `GET` | `/frame/{source}.jpg` | Singolo snapshot JPEG Set-of-Mark |
| `GET` | `/state/{source}` | Ultimo JSON completo (`objects`, `coords_mm`, `dog_telemetry`, `safety_stop`) |
| `WS` | `/ws/objects/{source}` | **WebSocket** continuo (default 15 Hz) con il JSON degli oggetti |
| `POST` | `/dog/target` | Imposta l'oggetto che il cane deve seguire (`{"target_id": 2}`) |
| `POST` | `/action/{source}/start` | Fotografa `frame_before` e `objects_before` prima di un'azione |
| `POST` | `/action/{source}/verify_local` | Esegue la verifica oggettiva locale sull'ultima azione |
| `POST` | `/voice/dispatch` | Esegue i tool di Grok Voice (`list_objects`, `select_object`, ecc.) sullo stato live |

---

## 🧪 Eseguire i Test

```bash
python -m pytest -v
```
