#!/usr/bin/env bash
# Start vision server + robot controller + arm bridge on the robot PC. Ctrl+C stops all three.
#
#   BRIDGE_KEY=some-secret ./bridge/start_stack.sh
#
# Env (all optional except BRIDGE_KEY):
#   BACKEND=robodk_sim|mock   controller backend (default: whatever config/robot.yaml says)
#   VISION=1|0                start computer-vision (default 1 if computer-vision/.venv exists)
#   VISION_MODE=realsense|webcam|synthetic   passed to the vision server as --mode
#   BRIDGE_PORT=8080
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
  (cd "$CV" && exec .venv/bin/python -m vision.eyes.cli run --port 8000 ${VISION_MODE:+--mode "$VISION_MODE"}) &
  pids+=($!)
  vision_url="http://127.0.0.1:8000"
  detector=(--detector vision_file --vision-file "$VISION_FILE")
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

echo "[stack] robot controller on 127.0.0.1:$CTRL_PORT (dashboard http://127.0.0.1:$CTRL_PORT/)"
(cd "$RC" && exec "$PY" -m robot_control.cli serve ${BACKEND:+--backend "$BACKEND"} "${detector[@]}" --port "$CTRL_PORT") &
pids+=($!)

for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$CTRL_PORT/health" >/dev/null && break; sleep 0.5; done

ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "[stack] arm bridge on :$BRIDGE_PORT  ->  groky-app/.env:"
echo "          EXPO_PUBLIC_ARM_URL=http://${ip:-<this-pc-ip>}:$BRIDGE_PORT"
[ -n "$vision_url" ] && echo "          EXPO_PUBLIC_VISION_SERVER=http://${ip:-<this-pc-ip>}:8000"
echo "[stack] operator reset after a stop/fault:  curl -X POST localhost:$CTRL_PORT/operator/reset -d '{\"operator\":\"you\"}'"
"$PY" "$ROOT/bridge/motiq_bridge.py" --port "$BRIDGE_PORT" --controller "http://127.0.0.1:$CTRL_PORT" \
  --vision "$vision_url" --vision-file "$VISION_FILE" &
pids+=($!)
wait -n
