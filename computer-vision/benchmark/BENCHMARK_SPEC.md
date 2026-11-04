# Benchmark: tokenizzazione A (MOTIQ) e B (Groky) per creare skill su RoboDK

Il contesto, l'ambiente e i bug noti sono in [HANDOFF.md](HANDOFF.md).

## Domanda
Quale dei due metodi di tokenizzazione produce token che portano a **skill corrette ed eseguibili su RoboDK**, e a quale costo (immagini inviate, token API, tempo)?

## Struttura: due livelli

### Livello 1 (principale): stessa pipeline a valle
L'unica variabile è la tokenizzazione:
1. Il metodo A e il metodo B tokenizzano lo stesso video.
2. Un **adattatore** converte i token di B nello schema `VideoTokens` di A. È una conversione deterministica: nessuna informazione inventata, campi mancanti lasciati a `null`.

   | B | A |
   |---|---|
   | `t_start_s` / `t_end_s` | `t_start` / `t_end` |
   | `motion_direction` | `motion.direction` |
   | ruolo `source` | ruolo `target` |
   | ruolo `container` | ruolo `dest` |
3. Entrambi i file di token passano nella **stessa** pipeline di A:
   ```
   .venv/bin/python -m vision.planner run --tokens <T.json> --executor robodk \
       --library <libreria nuova per ogni run> --runs-dir benchmark/results/<run>/
   ```
4. Le posizioni degli oggetti vengono dalla stazione RoboDK, uguale per i due metodi.

### Livello 2 (secondario): ogni pipeline nativa su RoboDK
B gira con il proprio planner ed executor, con le sole correzioni minime documentate nell'harness (vedi i bug in HANDOFF). Serve a misurare quanto B funziona così com'è. Non serve a confrontare la tokenizzazione.

## Dataset
Prepara `benchmark/videos/` con 4–6 video da 5–20 s. Non serve audio.

**Video principale: `whatsapp_demo`.** È il video reale registrato dall'utente, `MOTIQ/WhatsApp Video 2026-11-04 at 6.46.24 PM.mp4`, copiato in `benchmark/videos/whatsapp_demo.mp4` (copia identica). Usa la copia: nel nome originale c'è uno spazio speciale (U+202F) prima di "PM", quindi il percorso scritto a mano non si apre.
- 15.4 s, 848×480, 29.85 fps, con traccia audio da ignorare. È ripreso con la camera a mano.
- Contenuto: la mano prende il cilindro arancione (0–2 s), lo solleva (2–3.75 s), lo tiene in aria girandolo (3.75–10.5 s), lo abbassa nel secchio azzurro (10.75–12.75 s) e lo rilascia (circa 13 s).
- Copre il caso `pick_place` e mette alla prova il troncamento di B, che vede solo 0–5 s e quindi non vede il rilascio.
- Ground truth: `whatsapp_demo.gt.json`. **È una bozza da far confermare all'utente prima del run completo.** Scena: `whatsapp_demo.scene.json`.
- Usa questo video al posto del video sintetico, che **non** fa parte del benchmark.

| id | contenuto | perché |
|---|---|---|
| `whatsapp_demo` | Video reale: pick & place del cilindro arancione nel secchio azzurro, 15.4 s | Caso base con skill già in libreria; il rilascio cade dopo i 5 s visti da B |
| `sample_arm` | `data/replays/sample_arm.mp4` (statico, 2 s) | Atteso: nessuna azione |
| `unscrew` | Mano che svita un tappo e lo mette nel secchio | Richiede una skill nuova (`svitare`) |
| `long_sequence` | 2–3 azioni distribuite su almeno 12 s | Mette alla prova il troncamento ai 5 s di B |
| (opzionale) `press`/`pour` | Premere un tasto o versare | Altra skill nuova |

I video reali vanno chiesti all'utente, che deve registrarli. Non generarli e non inventarli.

Per ogni video servono due file:
- `<id>.gt.json`, la ground truth: `{"actions": [{"t_start", "t_end", "canonical", "verb", "target", "dest"}], "objects": [...], "expected_new_skills": [...]}`. Va annotata a mano o fatta confermare all'utente.
- `<id>.scene.json`, la scena RoboDK: lista di `SceneObject` (`name`, `color`, `label`, `position_mm` nel frame base) che riproduce la scena del video. Si usa con `python -m vision.planner.robodk_setup --scene`.

Regole di matching per la ground truth:
- Le azioni con `"optional": true` non contano nel recall.
- Un token che si sovrappone a un'azione opzionale con una `canonical` presente in `accept_canonical` non conta come falso positivo.
- Gli oggetti si abbinano tramite `objects[].synonyms`.

Nota sulla scena: `robodk_setup` non conosce il colore `arancione` né la label `cylinder`, quindi crea una scatola di 50 mm con il colore di default. Va bene così; non modificare il codice di A.

## Equità tra i due metodi
- **Modello:** lo stesso per entrambi, `XAI_MODEL=x-ai/grok-4.7` via OpenRouter.
- **Reasoning:** A manda `reasoning.effort` se `GROK_REASONING` è impostato, B non lo manda mai. Lancia A con `GROK_REASONING=` vuoto.
- **Temperatura:** A usa 0.2, B usa 0.1. Nel Livello 1 conta solo quella della tokenizzazione: annota la differenza nel report.
- **Risoluzione:** A manda frame HQ a 720p, B a 640 px sul lato lungo. Annotalo, oppure allinea A con `TokenizerConfig(hq_height=360)` per 640×360 se l'harness lo importa da Python.
- **Audio:** sempre `--no-voice` con A.
- **Libreria e stazione:** a ogni run una libreria nuova (solo builtin) e una stazione resettata (cancella gli oggetti, rilancia `robodk_setup`, robot in home).
- **Ripetizioni:** 3 per ogni combinazione video × metodo, perché Grok non è deterministico. Riporta media e deviazione standard.

## Metriche
Registra tutto in `benchmark/results/<YYYYMMDD_HHMM>/results.jsonl`, una riga per run.

**Tokenizzazione**

| Metrica | Come si misura |
|---|---|
| `n_images` | Numero di immagini inviate a Grok |
| `payload_kb` | Dimensione delle immagini inviate |
| `n_calls` | Numero di chiamate a Grok |
| `latency_s` | Tempo della tokenizzazione |
| `usage` | Prompt e completion token restituiti da OpenRouter in `usage`. A li salva in `GrokClient.calls`; per B vanno letti nell'harness. |
| `coverage_s` | Ultimo istante del video visto da Grok rispetto alla durata totale |
| `action_precision` / `action_recall` | Un token è corretto se ha la stessa `canonical` di un'azione della ground truth e IoU temporale ≥ 0.3 |
| `temporal_iou` | IoU temporale medio sulle coppie abbinate |
| `order_correct` | Distanza di edit tra la sequenza `canonical` dei token e quella della ground truth |
| `object_match` | Frazione di oggetti della ground truth riconosciuti, anche con sinonimi. Le scelte di matching vanno annotate. |

**Creazione delle skill** (Livello 1, pipeline di A)
- `plan_iterations`: iterazioni necessarie per ottenere un piano valido;
- `missing_detected`: skill nuove richieste da Grok, confrontate con `expected_new_skills`;
- `synth_ok`: skill sintetizzate che superano la validazione;
- errori del validatore.

**Esecuzione su RoboDK**
- `run_status` e step completati sul totale;
- `task_success`: posizione finale dell'oggetto letta con `RoboDKExecutor.object_pose()`, cioè dentro il contenitore in xy entro metà del lato e alla quota attesa;
- errori di IK.

## Harness da scrivere (in `computer-vision/benchmark/`)
1. **`groky_tokenize.py`:** gira con il venv di B. Aggiunge `Groky_Planner_Agent` a `sys.path`, chiama `sample_video_frames` e fa una sola chiamata con `TOKENIZER_SYSTEM_PROMPT` e i primi 10 frame, esattamente come `agent.py`. Salva token, `usage`, latenza e numero di immagini. Non deve eseguire nessuna pianificazione.
2. **`motiq_tokenize.py`:** usa `VideoTokenizer` di A con `use_voice=False` e salva le stesse metriche.
3. **`adapt_groky_tokens.py`:** conversione dallo schema di B a quello di A.
4. **`run_benchmark.py`:** cicla su video × metodo × ripetizione.
   - Prima di ogni run resetta la stazione RoboDK.
   - Lancia la pipeline di A con `--tokens` (Livello 1) e, separatamente, B nativo (Livello 2).
   - Calcola le metriche rispetto alla ground truth e scrive `results.jsonl`.
   - Ha un'opzione `--dry-run` che stima numero di chiamate e costo senza chiamare Grok.
5. **`report.py`:** produce `report.md` con una tabella per livello (media ± std), le osservazioni qualitative (skill sintetizzate a confronto, errori tipici) e una raccomandazione motivata.

## Ordine consigliato
1. Fai confermare all'utente la bozza `whatsapp_demo.gt.json`. Poi prova A e B a mano su `whatsapp_demo` per entrambi i livelli e annota subito i problemi.
2. Scrivi l'harness e fai un `--dry-run`, poi chiedi conferma all'utente sul costo.
3. Fai il run completo con 3 ripetizioni.
4. Scrivi il report.

## Fuori scope
- Audio e STT.
- Modificare il codice di A o di B. Le eccezioni sono le correzioni minime nell'harness di B, documentate, oppure un bug di A trovato durante il benchmark, che va segnalato all'utente prima di correggerlo.
- Robot reale.
- Integrazione FastAPI e `demo_live.py`.

## Consegna
Alla fine devono esistere:
- `benchmark/results/<run>/report.md` e `results.jsonl`;
- i token di ogni run;
- i run log di A.

All'utente va un riassunto breve: quale metodo vince e su cosa, quanto costa ciascuno, quali limiti ha B così com'è.
