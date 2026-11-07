"""System prompt e builder dei messaggi utente per i tre compiti di Grok."""
from __future__ import annotations

import json
from typing import Any, Optional

from .primitives import Catalog

RULES = """REGOLE FISICHE DEL BRACCIO (UR5e con pinza parallela, vista dall'alto del tavolo):
- Aprire la pinza (OPEN_GRIPPER) PRIMA di scendere su un oggetto da prendere.
- Salire (MOVE_UP / MOVE_ABOVE) PRIMA di qualsiasi traslazione orizzontale: mai traslare a quota bassa.
- Mai scendere sotto il tavolo: le discese devono restare sopra min_z_above_table_m.
- Rispettare min/max dei parametri e i limiti operativi (velocità, passo massimo).
- Una rotazione della pinza è al massimo 90° per primitiva: per giri completi usa `repeat`
  ruotando, riaprendo, ruotando indietro e richiudendo (regrasp).
- Ogni parametro sconosciuto o stimato va dichiarato in `uncertainties` (verrà validato)."""


TOKENIZE_SYSTEM = """Sei il modulo di tokenizzazione video di un sistema di programmazione robotica
da dimostrazione. Ricevi una sequenza di KEYFRAME (immagini) di una persona che esegue un compito
con le mani su un tavolo, con indice di frame e timestamp, ed eventualmente le frasi pronunciate.
Devi segmentare la dimostrazione in TOKEN D'AZIONE atomici, nell'ordine temporale.

Per ogni token indica:
- verb: verbo italiano all'infinito che descrive l'azione in modo specifico (es. "svitare",
  "prendere", "versare", "premere", "inserire"). Usa le parole dell'operatore se le dice.
- canonical: una tra grasp|release|move|rotate|insert|press|push|pour|wait|unknown.
- objects: oggetti coinvolti con name (breve, inglese snake_case se possibile, es. "white_cap"),
  color, role (target = oggetto manipolato, dest = destinazione/contenitore), bbox_norm [x,y,w,h].
- gripper: stato della mano (open|closed|unknown) alla fine del token.
- motion: direzione dominante (up|down|left|right|forward|backward|toward_dest|none) e distanza
  stimata in cm, dal punto di vista di chi guarda il tavolo dalla camera.
- rotation: se la mano ruota, asse (yaw = attorno alla verticale) e angolo TOTALE in gradi
  (positivo = antiorario visto dall'alto).
- t_start, t_end, frame_idx: dai timestamp/indici dei keyframe che mostrano l'azione.
- speech: la frase pronunciata durante l'azione, se c'è. confidence 0..1. notes: dettagli utili.
Riporta anche scene_objects: tutti gli oggetti visibili (name, color, label).

Rispondi SOLO con JSON:
{"tokens":[{"verb":"...","canonical":"...","t_start":0.0,"t_end":0.0,"frame_idx":[..],
"objects":[{"name":"...","color":"...","role":"target"}],"gripper":"closed",
"motion":{"direction":"up","distance_cm":10},"rotation":null,"speech":null,
"confidence":0.8,"notes":""}],
"scene_objects":[{"name":"...","color":"...","label":"..."}]}"""


PLAN_SYSTEM = """Sei l'agente di pianificazione di un braccio robotico UR5e simulato in PyBullet.
Ricevi i TOKEN D'AZIONE estratti da un video dimostrativo, la LIBRERIA DI SKILL disponibili
(con firme e parametri) e gli OGGETTI presenti nella stazione.
Devi produrre un piano come sequenza ordinata di CHIAMATE A SKILL che riproduca l'obiettivo
della dimostrazione sul robot.

Regole:
- Usa solo skill della libreria quando coprono l'azione (es. pick, place, pick_and_place, home).
- Se un'azione del video NON è coperta da nessuna skill (es. svitare, versare, premere un tasto),
  aggiungila a `missing_skills` con un nome snake_case all'infinito (es. "svitare"), una
  descrizione precisa del gesto osservato, i token_ids di riferimento e i parametri proposti;
  includila comunque in `calls` nel punto giusto con quel nome.
- Gli argomenti object_ref devono usare i NOMI ESATTI degli oggetti della stazione quando
  disponibili (altrimenti i nomi dei token).
- Non riprodurre i movimenti di avvicinamento della mano: sono già dentro le skill.
- `pick` SOLLEVA l'oggetto dopo la presa. Per azioni da fare sul posto con l'oggetto ancora
  vincolato (svitare, ruotare una manopola, premere, spingere) usa `grasp`, che afferra senza
  sollevare, prima della skill d'azione; la skill d'azione poi solleva se serve.
- Usa negli args SOLO i parametri dichiarati nella firma della skill.
- Termina con `home`.
- Se ricevi ERRORI di un tentativo precedente, correggi il piano di conseguenza.

Rispondi SOLO con JSON:
{"goal":"...","calls":[{"skill":"pick","args":{"target":"red_cube"},"token_ids":["tok_001"],
"reason":"..."}],"missing_skills":[{"name":"svitare","description":"...","token_ids":["tok_002"],
"proposed_params":{"target":{"type":"object_ref"},"turns":{"type":"integer","default":4,"min":1,"max":10}}}],
"notes":"..."}"""


def synthesize_system(catalog: Catalog) -> str:
    lim = catalog.robot.limits
    return f"""Sei il modulo di sintesi skill di un braccio UR5e simulato in PyBullet.
Devi definire una NUOVA SKILL riutilizzabile, osservata in un video dimostrativo, come
COMPOSIZIONE di primitive del catalogo chiuso qui sotto (ed eventualmente di skill esistenti).
Non puoi inventare primitive né scrivere codice.

CATALOGO PRIMITIVE (unità: metri, gradi; frame base del robot):
{json.dumps(catalog.compact(), ensure_ascii=False)}

LIMITI OPERATIVI: {json.dumps(lim)}

{RULES}

Struttura del body (lista ordinata), ogni elemento è UNO tra:
- {{"primitive": "<ID>", "args": {{...}}}}
- {{"skill": "<skill_id esistente>", "args": {{...}}}}
- {{"repeat": <intero 0..10 oppure "$param">, "body": [ ... ]}}
Negli args puoi usare "$nome_param" per riferirti ai parametri della skill.
I parametri della skill: type in number|integer|object_ref|enum|string, con default/min/max.
Gli oggetti si passano come object_ref e si usano con MOVE_ABOVE {{"target": "$param"}}.

Rispondi SOLO con JSON:
{{"id":"svitare","name":"Svitare","description":"...","aliases":["svitare","unscrew"],
"params":{{"target":{{"type":"object_ref"}},"turns":{{"type":"integer","default":4,"min":1,"max":10}}}},
"body":[...],"preconditions":["..."],"postconditions":["..."],"uncertainties":["..."]}}"""


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, indent=None)


def tokenize_user(frames: list[dict[str, Any]], phrases: list[dict[str, Any]],
                  previous: list[dict[str, Any]], goal_hint: Optional[str]) -> str:
    lines = ["KEYFRAME in questo chunk (nell'ordine delle immagini allegate):"]
    for k, f in enumerate(frames, 1):
        lines.append(f"- immagine {k}: frame idx={f['idx']} t={f['t']:.3f}s motivo={f['reason']}")
    if phrases:
        lines.append("\nFRASI PRONUNCIATE vicino a questi istanti:")
        for p in phrases:
            lines.append(f"- t={p['t_start']:.2f}-{p['t_end']:.2f}s: \"{p['text']}\"")
    if previous:
        lines.append("\nULTIMI TOKEN DEL CHUNK PRECEDENTE (per continuità, non ripeterli):")
        lines.append(_dump(previous))
    if goal_hint:
        lines.append(f"\nOBIETTIVO DICHIARATO: {goal_hint}")
    return "\n".join(lines)


def plan_user(tokens: list[dict[str, Any]], transcript: str, signatures: list[dict[str, Any]],
              scene: list[dict[str, Any]], goal: Optional[str], errors: list[str]) -> str:
    parts = [f"OBIETTIVO: {goal or '(dedurlo dalla dimostrazione)'}",
             f"\nTOKEN D'AZIONE:\n{_dump(tokens)}"]
    if transcript:
        parts.append(f"\nTRASCRIZIONE: \"{transcript}\"")
    parts.append(f"\nLIBRERIA SKILL:\n{_dump(signatures)}")
    parts.append(f"\nOGGETTI NELLA STAZIONE:\n{_dump(scene) if scene else '(nessuno noto)'}")
    if errors:
        parts.append("\nERRORI DEL TENTATIVO PRECEDENTE (correggili):\n- " + "\n- ".join(errors))
    return "\n".join(parts)


def synthesize_user(missing: dict[str, Any], tokens: list[dict[str, Any]],
                    signatures: list[dict[str, Any]], errors: list[str]) -> str:
    parts = [f"SKILL DA DEFINIRE:\n{_dump(missing)}",
             f"\nTOKEN DEL VIDEO CHE LA MOSTRANO:\n{_dump(tokens)}",
             f"\nSKILL ESISTENTI (riusabili):\n{_dump(signatures)}"]
    if errors:
        parts.append("\nLA TUA PROPOSTA PRECEDENTE NON È VALIDA (correggi):\n- " + "\n- ".join(errors))
    return "\n".join(parts)
