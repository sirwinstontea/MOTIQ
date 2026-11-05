#!/usr/bin/env bash
# Start vision server + robot controller + arm bridge on the robot PC. Ctrl+C stops all three.
#
#   BRIDGE_KEY=some-secret ./bridge/start_stack.sh
#
# Env (all optional except BRIDGE_KEY):
#   BACKEND=robodk_sim|mock   controller backend (default: whatever config/robot.yaml says)
#   VISION=1|0                start computer-vision (default 1 if computer-vision/.venv exists)
#   VISION_MODE=realsense|webcam|synthetic   passed to the vision server as --mode
#   VISION_ROBOT_IP=192.168.7.20   read-only UR telemetry (rtde_receive) for Skills -> Calibration; never moves the arm
#   BRIDGE_PORT=8080
#   TEACH_PORT=8001           teach server (demonstration video -> skill), started with the vision server
#   DIGITAL_TWIN_CONFIG=config/digital_twin.sim.json   start the READ-ONLY digital twin service (127.0.0.1:8770,
#                             relative to robot_controller/) for Skills -> Sync Digital Twin. Mirror only.
#   SIM=1                     simulation only, on the arm selected in the app (Robots -> Arm catalog). UR5e: the bridge
#                             sends step/joint/down-up moves to URSim (127.0.0.1, mirrored into RoboDK) instead of the
#                             physical UR. Other arms (UR3e, xArm5, CS63, CS66): their own RoboDK cell, named moves only.
#                             Selecting another arm switches RoboDK + controller + bridge within a few seconds.
#   RESET_STATION=0           with SIM=1: keep RoboDK's objects where they are (default: reload the station, so every
#                             object is back at its start pose)
#   CHOOSER=1|0               start robot-chooser (arm catalog, 127.0.0.1:8771) for the app's Robots -> Arm catalog
#                             (default 1). Config files only; with SIM=1 a new selection is applied live, otherwise on restart.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RC="$ROOT/robot_controller"
CV="$ROOT/computer-vision"
PY="$RC/.venv/bin/python"
VISION_FILE="$RC/runs/vision_bridge.json"
BRIDGE_PORT="${BRIDGE_PORT:-8080}"
CTRL_PORT="${CTRL_PORT:-8765}"

: "${BRIDGE_KEY:?set BRIDGE_KEY (8+ chars); put the same value in groky-app/.env as EXPO_PUBLIC_BRIDGE_KEY}"
export BRIDGE_KEY

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

VISION="${VISION:-$([ -x "$CV/.venv/bin/python" ] && echo 1 || echo 0)}"
vision_url=""
if [ "$VISION" = "1" ]; then
  echo "[stack] vision server on :8000"
  (cd "$CV" && exec .venv/bin/python -m vision.eyes.cli run --port 8000 ${VISION_MODE:+--mode "$VISION_MODE"} ${VISION_ROBOT_IP:+--robot-ip "$VISION_ROBOT_IP"}) &
  pids+=($!)
  vision_url="http://127.0.0.1:8000"
  detector=(--detector vision_file --vision-file "$VISION_FILE")
  # teach server: the app's Show Motion / Upload video send demonstrations here; learned skills are kept in
  # computer-vision/data/skills/learned/
  echo "[stack] teach server on :${TEACH_PORT:-8001}"
  (cd "$CV" && exec .venv/bin/python "$ROOT/computer-vision-to-app/teach_server.py" --port "${TEACH_PORT:-8001}") &
  pids+=($!)
else
  echo "[stack] no vision (run computer-vision/setup.sh to enable); controller uses the MOCK detector"
  detector=(--detector mock)
fi

if curl -s -o /dev/null "http://127.0.0.1:$CTRL_PORT/health"; then
  echo "[stack] port $CTRL_PORT is taken (a 'robot_control.cli demo --dashboard' still running?). Stop it or set CTRL_PORT." >&2
  exit 1
fi
# RoboDK first, as its own process (it keeps running after Ctrl+C), then the controller connects to it.
# Letting the controller launch it mid-connect drops the API socket ("Broken pipe").
if [ "${BACKEND:-}" != "mock" ] && ! (echo > /dev/tcp/127.0.0.1/20500) 2>/dev/null; then
  echo "[stack] starting RoboDK..."
  (cd "$RC" && setsid nohup "$HOME/RoboDK/RoboDK-Start.sh" stations/UR5e_demo.rdk >/dev/null 2>&1 &)
  for _ in $(seq 1 90); do (echo > /dev/tcp/127.0.0.1/20500) 2>/dev/null && break; sleep 1; done
  sleep 5   # let it finish loading the station
fi

if [ -n "${DIGITAL_TWIN_CONFIG:-}" ]; then
  echo "[stack] digital twin (MIRROR ONLY, no physical motion) on 127.0.0.1:8770, config $DIGITAL_TWIN_CONFIG"
  (cd "$RC" && exec "$PY" -m robot_control.digital_twin.cli serve --config "$DIGITAL_TWIN_CONFIG") &
  pids+=($!)
fi

if [ "${CHOOSER:-1}" = "1" ]; then
  echo "[stack] robot-chooser (arm catalog, config files only) on 127.0.0.1:8771"
  (cd "$ROOT/robot-chooser" && exec "$PY" -m robot_chooser serve --port 8771) &
  pids+=($!)
fi

# SIM=1: the arm = the robot-chooser selection (app: Robots -> Arm catalog).
#   UR5e (or a BLOCKED selection) -> the UR5e reference cell on URSim -> RoboDK (config/robot.ursim_demo.yaml;
#                                    the chooser never generates ursim_sync, safety contract 9.1)
#   any other READY arm           -> its own RoboDK cell (stations/<Model>_demo.rdk, chooser-generated configs),
#                                    driven by the controller in RoboDK only (robodk_sim, RUNMODE_SIMULATE)
# Selecting another arm in the app restarts the controller + bridge on it (a few seconds, RoboDK switches cell).
SEL="$RC/config/selection.yaml"
sel_field() { awk -v k="$1:" '$1==k {print $2; exit}' "$SEL" 2>/dev/null; }
sel_key() { echo "$(sel_field robot_id)/$(sel_field status)"; }
[ "${SIM:-0}" = "1" ] && detector=(--detector mock)   # the objects are RoboDK station items, the real camera can't see them

pick_arm() {   # sets ctrl_cfg, bridge_scene, station, sim_arm
  ctrl_cfg=() bridge_scene=() station="" sim_arm=UR5e
  if [ "${SIM:-0}" = "1" ]; then
    local rid model status
    rid="$(sel_field robot_id)" model="$(sel_field model)" status="$(sel_field status)"
    if [ -n "$rid" ] && [ "$rid" != ur_ur5e ] && [ "$status" = READY ] && [ -f "$RC/stations/${model}_demo.rdk" ]; then
      ctrl_cfg=(--backend robodk_sim --config config/robot.yaml --safety config/safety.yaml --scene config/scene.yaml)
      bridge_scene=(--scene "$RC/config/scene.yaml")
      station="stations/${model}_demo.rdk" sim_arm="$model"
      echo "[stack] SIM=1: $model ($rid) cell in RoboDK ($station), controller drives RoboDK directly"
    else
      [ -n "$rid" ] && [ "$rid" != ur_ur5e ] &&
        echo "[stack] WARNING: selected arm $rid is $status or has no stations/${model}_demo.rdk - showing the UR5e" >&2
      ctrl_cfg=(--backend ursim_sync --config config/robot.ursim_demo.yaml --safety config/safety.ursim_demo.yaml
                --scene config/scene.example.yaml)
      bridge_scene=(--scene "$RC/config/scene.example.yaml")
      station="stations/UR5e_demo.rdk"
      echo "[stack] SIM=1: UR5e demo cell on URSim -> RoboDK (config/robot.ursim_demo.yaml)"
    fi
  elif [ -n "${BACKEND:-}" ]; then
    ctrl_cfg=(--backend "$BACKEND")
  fi
}

start_arm() {   # controller + bridge for the current selection; sets ctrl_pid, bridge_pid
  pick_arm
  # fresh table every run (and every arm switch): RoboDK shows only this arm's cell, objects at their start poses
  if [ -n "$station" ] && [ "${RESET_STATION:-1}" = "1" ]; then
    (cd "$RC" && "$PY" scripts/reset_station.py --station "$station") ||
      echo "[stack] WARNING: could not load $station in RoboDK" >&2
  fi
  echo "[stack] robot controller on 127.0.0.1:$CTRL_PORT (dashboard http://127.0.0.1:$CTRL_PORT/)"
  (cd "$RC" && exec "$PY" -m robot_control.cli serve "${ctrl_cfg[@]}" "${detector[@]}" --port "$CTRL_PORT") &
  ctrl_pid=$!
  local up=0
  for _ in $(seq 1 120); do
    curl -sf "http://127.0.0.1:$CTRL_PORT/health" >/dev/null && { up=1; break; }
    kill -0 "$ctrl_pid" 2>/dev/null || break      # controller exited (bad config): stop now, don't wait it out
    sleep 0.5
  done
  if [ "$up" != 1 ]; then
    echo "[stack] ERROR: the robot controller did not start (see its error above). If it says robot-chooser selected a" >&2
    echo "        BLOCKED arm: pick a Ready arm in the app (Robots -> Arm catalog) or run" >&2
    echo "        (cd robot-chooser && ../robot_controller/.venv/bin/python -m robot_chooser select ur_ur5e)" >&2
    return 1
  fi
  "$PY" "$ROOT/bridge/motiq_bridge.py" --port "$BRIDGE_PORT" --controller "http://127.0.0.1:$CTRL_PORT" \
    --vision "$vision_url" --vision-file "$VISION_FILE" "${bridge_scene[@]}" --sim-arm "$sim_arm" &
  bridge_pid=$!
}

stop_arm() {
  kill "$bridge_pid" "$ctrl_pid" 2>/dev/null || true
  wait "$bridge_pid" "$ctrl_pid" 2>/dev/null || true
  for _ in $(seq 1 20); do        # the next controller/bridge needs the ports
    ss -Hltn "sport = :$CTRL_PORT" | grep -q . || ss -Hltn "sport = :$BRIDGE_PORT" | grep -q . || break
    sleep 0.25
  done
}

cleanup() { kill "${pids[@]}" "${bridge_pid:-}" "${ctrl_pid:-}" 2>/dev/null || true; wait 2>/dev/null || true; }
cur_sel="$(sel_key)"
start_arm || exit 1

ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "[stack] arm bridge on :$BRIDGE_PORT  ->  groky-app/.env:"
echo "          EXPO_PUBLIC_ARM_URL=http://${ip:-<this-pc-ip>}:$BRIDGE_PORT"
[ -n "$vision_url" ] && echo "          EXPO_PUBLIC_VISION_SERVER=http://${ip:-<this-pc-ip>}:8000"
echo "[stack] operator reset after a stop/fault:  curl -X POST localhost:$CTRL_PORT/operator/reset -d '{\"operator\":\"you\"}'"

if [ "${SIM:-0}" != "1" ]; then
  wait -n
  exit
fi
# SIM=1: follow the arm selection; exit (like wait -n) when any part dies on its own
while sleep 2; do
  for p in "${pids[@]}" "$ctrl_pid" "$bridge_pid"; do
    kill -0 "$p" 2>/dev/null || { echo "[stack] a part of the stack exited (pid $p)" >&2; exit 1; }
  done
  new_sel="$(sel_key)"
  if [ "$new_sel" != "$cur_sel" ]; then
    echo "[stack] arm selection changed ($cur_sel -> $new_sel): restarting controller + bridge"
    cur_sel="$new_sel"
    stop_arm
    start_arm || exit 1
  fi
done
