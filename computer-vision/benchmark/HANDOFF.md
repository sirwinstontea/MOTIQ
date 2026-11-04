# Handoff: benchmark dei metodi di tokenizzazione video

Leggi prima questo file, poi [BENCHMARK_SPEC.md](BENCHMARK_SPEC.md), che descrive cosa fare in pratica.
Stato al **4 novembre 2026**.

## Il compito
Confrontare **due metodi di tokenizzazione video** per creare skill robotiche, testandoli su **RoboDK** (UR5e simulato) con **Grok via OpenRouter**. **Niente audio**: la parte vocale non funziona ancora, quindi va disattivata in tutti i run.

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
- `executors/robodk_exec.py` per l'esecuzione su RoboDK.

A è coperto da 34 test: `GROK_MOCK=1 .venv/bin/python -m pytest tests/test_planner.py`.

## Ambiente già pronto
- **Python di A:** `computer-vision/.venv` (Python 3.12) con pydantic, httpx, numpy, robodk 6.0.2, imageio-ffmpeg e pytest. Sul sistema ffmpeg non c'è, quindi A usa il binario di `imageio-ffmpeg`. **OpenCV non è installato**: per B crea un venv separato in `Groky_Planner_Agent/.venv` con `pip install -r planner/requirements.txt`, senza toccare quello di A.
- **OpenRouter:** la chiave è in `computer-vision/.env` (`XAI_API_KEY`), con `XAI_BASE_URL=https://openrouter.ai/api/v1` e `XAI_MODEL=x-ai/grok-4.7`. Non stampare mai la chiave.
  - A legge `.env` da solo.
  - **B non lo legge**: esporta le variabili prima di lanciarlo, con `set -a; source /Users/riccardoragnatela/MOTIQ/MOTIQ/computer-vision/.env; set +a`. Senza chiave B passa in modalità mock **senza dirlo**. Inoltre il suo modello di default è `grok-2-vision-1212`, quindi serve `XAI_MODEL`.
- **RoboDK:** versione 6.0.7, installata in `~/RoboDK/RoboDK.app`. L'API risponde su `localhost:20500` solo quando RoboDK è aperto.
  - **Stazione salvata:** [`station_ur5e.rdk`](station_ur5e.rdk) contiene UR5e, tool `Gripper` (TCP a 150 mm), `white_cap` e `blue_bucket`. Si apre da RoboDK con File > Open, oppure con `Robolink().AddFile(path)`.
  - **Modello del robot:** se manca, scaricalo da `https://cdn.robodk.com/downloads-library/library-robots/UR5e.robot` in una cartella vuota e caricalo con `Robolink().AddFile(path)`.
  - **Setup della scena:** `.venv/bin/python -m vision.planner.robodk_setup --scene <scene.json>` crea la pinza e gli oggetti (scatole, con l'origine sul fondo e posizione nel frame base del robot) e porta il robot in home.
  - **Pulizia:** per azzerare la scena, cancella gli oggetti con `ItemList(ITEM_TYPE_OBJECT)` e poi `.Delete()`, quindi rilancia il setup.
  - **Home del TCP:** (−492, −133, 338) mm, con la pinza rivolta verso il basso.

## Risultati già verificati con A (riferimento)
- **Pianificazione:** con `tests/fixtures/planner/tokens_unscrew.json` su RoboDK e Grok reale, il piano è `grasp → svitare → place → home`.
  - Grok ha sintetizzato `svitare`: 4 quarti di giro con ripresa della presa.
  - Risultato: 26 step su 26, e il tappo finisce nel secchio.
- **Video sintetico** (quadrato rosso che va a destra, si ferma e torna a sinistra): è solo un test di sviluppo e **non va usato nel benchmark**, che usa il video reale `benchmark/videos/whatsapp_demo.mp4`. Risultato: A trova 2 segmenti e 8 keyframe. Grok restituisce `spostare(right)`, `attendere`, `spostare(left)`.
- **`data/replays/sample_arm.mp4`:** dura 2 s ed è statico. A manda 3 keyframe uniformi, Grok non trova azioni e il piano è solo `home`.

## Bug noti di B (rilevati leggendo il codice, non ancora eseguito)
Riportali come risultati del benchmark. **Non correggere i file di B.** Le correzioni minime necessarie per farlo girare vanno nell'harness del benchmark e vanno documentate.
1. **Percorso della libreria sbagliato:** `DEFAULT_LIBRARY_PATH` sale di una cartella di troppo e punta a `MOTIQ/MOTIQ/data/skills/skill_library.json`, che non esiste, quindi la libreria risulta **vuota**. Passa `SkillLibrary(path=<Groky>/data/skills/skill_library.json)`. Anche i run log finiscono fuori dalla cartella di Groky, in `MOTIQ/MOTIQ/logs/runs`.
2. **Tokenizzazione troncata:** solo i primi 10 frame, quindi tutto ciò che succede dopo 5 s viene ignorato.
3. **Executor RoboDK di B:**
   - `MOVE_ABOVE` usa `PoseAbs()`, cioè il frame della stazione invece della base del robot, e un orientamento fisso `rotx(pi)`;
   - usa `MoveL` anche per i trasferimenti lunghi (in A attraversavano la zona della base e servivano movimenti giunti);
   - non controlla la cinematica inversa;
   - ritorna sempre `success=True`, quindi un errore di RoboDK solleva un'eccezione che interrompe il run;
   - la home `[0,-90,0,-90,0,0]` è diversa da quella del catalogo `[0,-90,90,-90,-90,0]`;
   - cerca gli oggetti con `rdk.Item(nome)` senza tipo.
4. **Nessun retry:** non ci sono nuovi tentativi né feedback degli errori a Grok. Se il JSON o la validazione falliscono, il run termina.
5. **Validatore approssimativo:** stima z con una costante dopo `MOVE_ABOVE` (`max(h, 0.05) + h`).

## Lezioni da A su RoboDK
Ti servono se scrivi un adattatore o devi capire un errore:
- `SolveIK(pose)` senza `tool` e `reference` interpreta la posa come quella della flangia, che con il tool montato sta 150 mm sopra il TCP. Passa sempre `robot.PoseTool()` e `robot.PoseFrame()`.
- Un `MoveL` dalla home (x<0) agli oggetti (x>0) passa sopra la base del robot. Per i trasferimenti usa IK seguito da `MoveJ`.
- `AddShape` con `add_to=<frame>` fallisce: crea l'oggetto senza parent e poi chiama `setParent`.
- `Robolink()` con RoboDK chiuso resta bloccato: controlla prima che la porta 20500 risponda.

## Regole
- **Audio:** usa sempre `--no-voice` con A. B non ha audio.
- **Robot reale:** non usarlo mai, solo `RUNMODE_SIMULATE`.
- **Costi OpenRouter:** stimali prima di lanciare il benchmark completo e chiedi conferma all'utente se superano qualche dollaro.
- **Video di prova:** il video principale è `benchmark/videos/whatsapp_demo.mp4`, il video reale dell'utente. La sua ground truth è una bozza da far confermare. Se servono altri video reali, ad esempio `unscrew` o `long_sequence`, chiedili all'utente invece di inventarli.
