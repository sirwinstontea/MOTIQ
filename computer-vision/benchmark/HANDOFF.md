# Handoff: benchmark dei metodi di tokenizzazione video

Leggi prima questo file, poi [BENCHMARK_SPEC.md](BENCHMARK_SPEC.md), che descrive cosa fare in pratica.
Stato al **4 novembre 2026**.

## Il compito
Confrontare **due metodi di tokenizzazione video** per creare skill robotiche, testandoli su **PyBullet** (UR5e simulato) con **Grok via OpenRouter**. **Niente audio**: la parte vocale non funziona ancora, quindi va disattivata in tutti i run.

## I due metodi

| | **A: MOTIQ** (`vision/planner`) | **B: Groky** (`Groky_Planner_Agent`) |
|---|---|---|
| Percorso | `/Users/riccardoragnatela/MOTIQ/MOTIQ/computer-vision/vision/planner/` | `/Users/riccardoragnatela/MOTIQ/MOTIQ/Groky_Planner_Agent/planner/` (spostato da `~/Downloads`) |
| Estrazione frame | ffmpeg a doppio flusso. Il flusso LQ (64×36, grigio, tutti i frame) misura il movimento; quello HQ (720p) ha lo stesso indice e lo stesso PTS. | OpenCV, campionamento uniforme a 2 fps, lato massimo 640 px |
| Selezione | Keyframe nei punti di inizio, picco e fine di ogni movimento, più il primo e l'ultimo frame. Se non c'è movimento, campionamento uniforme a 1 s. | Nessuna: manda **solo i primi 10 frame** (`frames[:10]` in `agent.py`), cioè i primi 5 s del video |
| Chiamate Grok | Chunk da 8 immagini, con i token del chunk precedente come contesto. Alla fine unisce i token adiacenti. | 1 sola chiamata |
| Comando | `python -m vision.planner tokenize --video V --out T.json --no-voice` | Nessun comando solo-tokenize: `python -m planner tokenize` esegue l'intera pipeline con un executor mock |
| Schema token | `VideoTokens` in `vision/planner/schemas.py` (`t_start`, `motion.direction`, ruoli `target`/`dest`) | `VideoTokens` in `planner/schemas.py` (`t_start_s`, `motion_direction`, ruoli `source`/`container`) |

Codice di A da conoscere:
- `frames.py`, `motion.py`, `tokenizer.py` per l'estrazione e la tokenizzazione;
- `agent.py` per il loop plan → learn → validate → execute;
- `executors/pybullet_exec.py` per l'esecuzione su PyBullet.

A è coperto da 34 test: `GROK_MOCK=1 .venv/bin/python -m pytest tests/test_planner.py`.

## Ambiente già pronto
- **Python di A:** `computer-vision/.venv` (Python 3.12) con pydantic, httpx, numpy, pybullet 6.0.2, imageio-ffmpeg e pytest. Sul sistema ffmpeg non c'è, quindi A usa il binario di `imageio-ffmpeg`. **OpenCV non è installato**: per B crea un venv separato in `Groky_Planner_Agent/.venv` con `pip install -r planner/requirements.txt`, senza toccare quello di A.
- **OpenRouter:** la chiave è in `computer-vision/.env` (`XAI_API_KEY`), con `XAI_BASE_URL=https://openrouter.ai/api/v1` e `XAI_MODEL=x-ai/grok-4.7`. Non stampare mai la chiave.
  - A legge `.env` da solo.
  - **B non lo legge**: esporta le variabili prima di lanciarlo, con `set -a; source /Users/riccardoragnatela/MOTIQ/MOTIQ/computer-vision/.env; set +a`. Senza chiave B passa in modalità mock **senza dirlo**. Inoltre il suo modello di default è `grok-2-vision-1212`, quindi serve `XAI_MODEL`.
- **PyBullet:** la cella vive nel controller (`robot_control/sim/world.py`). Non c'è un'app da aprire e non c'è una porta API. Home del TCP: (−492, −133, 338) mm, pinza verso il basso.

## Risultati già verificati con A (riferimento)
- **Pianificazione:** con `tests/fixtures/planner/tokens_unscrew.json` su PyBullet e Grok reale, il piano è `grasp → svitare → place → home`.
  - Grok ha sintetizzato `svitare`: 4 quarti di giro con ripresa della presa.
  - Risultato: 26 step su 26, e il tappo finisce nel secchio.
- **Video sintetico** (quadrato rosso che va a destra, si ferma e torna a sinistra): è solo un test di sviluppo e **non va usato nel benchmark**, che usa il video reale `benchmark/videos/whatsapp_demo.mp4`. Risultato: A trova 2 segmenti e 8 keyframe. Grok restituisce `spostare(right)`, `attendere`, `spostare(left)`.
- **`data/replays/sample_arm.mp4`:** dura 2 s ed è statico. A manda 3 keyframe uniformi, Grok non trova azioni e il piano è solo `home`.

## Bug noti di B (rilevati leggendo il codice, non ancora eseguito)
Riportali come risultati del benchmark. **Non correggere i file di B.** Le correzioni minime necessarie per farlo girare vanno nell'harness del benchmark e vanno documentate.
1. **Percorso della libreria sbagliato:** `DEFAULT_LIBRARY_PATH` sale di una cartella di troppo e punta a `MOTIQ/MOTIQ/data/skills/skill_library.json`, che non esiste, quindi la libreria risulta **vuota**. Passa `SkillLibrary(path=<Groky>/data/skills/skill_library.json)`. Anche i run log finiscono fuori dalla cartella di Groky, in `MOTIQ/MOTIQ/logs/runs`.
2. **Tokenizzazione troncata:** solo i primi 10 frame, quindi tutto ciò che succede dopo 5 s viene ignorato.
3. **Executor PyBullet di B:**
   - `MOVE_ABOVE` usa `PoseAbs()`, cioè il frame della stazione invece della base del robot, e un orientamento fisso `rotx(pi)`;
   - usa `MoveL` anche per i trasferimenti lunghi (in A attraversavano la zona della base e servivano movimenti giunti);
   - non controlla la cinematica inversa;
   - ritorna sempre `success=True`, quindi un errore di PyBullet solleva un'eccezione che interrompe il run;
   - la home `[0,-90,0,-90,0,0]` è diversa da quella del catalogo `[0,-90,90,-90,-90,0]`;
   - cerca gli oggetti con `rdk.Item(nome)` senza tipo.
4. **Nessun retry:** non ci sono nuovi tentativi né feedback degli errori a Grok. Se il JSON o la validazione falliscono, il run termina.
5. **Validatore approssimativo:** stima z con una costante dopo `MOVE_ABOVE` (`max(h, 0.05) + h`).

## Regole
- **Audio:** usa sempre `--no-voice` con A. B non ha audio.
- **Robot reale:** il planner non lo comanda. Il braccio simulato è la cella PyBullet del controller.
- **Costi OpenRouter:** stimali prima di lanciare il benchmark completo e chiedi conferma all'utente se superano qualche dollaro.
- **Video di prova:** il video principale è `benchmark/videos/whatsapp_demo.mp4`, il video reale dell'utente. La sua ground truth è una bozza da far confermare. Se servono altri video reali, ad esempio `unscrew` o `long_sequence`, chiedili all'utente invece di inventarli.
