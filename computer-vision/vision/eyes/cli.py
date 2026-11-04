"""Riga di comando (CLI) per Visione A ("Occhi").

Esempi d'uso:
  python -m vision.eyes.cli inspect --mode synthetic
  python -m vision.eyes.cli generate-replay --output data/replays/sample_arm.mp4
  python -m vision.eyes.cli calibrate-arm --generate-board logs/aruco_a4.png   # foglio da stampare
  python -m vision.eyes.cli calibrate-arm      # dalla camera (RealSense se c'è), marker in data/calibration/markers_mm.json
  python -m vision.eyes.cli run --port 8000                  # RealSense USB-C, se no webcam del PC
  python -m vision.eyes.cli run --mode synthetic --port 8000
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import uvicorn

from .calibration.aruco_calib import ArmCalibrator, load_marker_coords, render_aruco_workspace
from .capture.reader import generate_sample_video
from .capture.realsense import default_camera
from .pipeline import EyePipeline
from .server.app import create_app


def _cmd_inspect(args: argparse.Namespace) -> int:
    pipeline = EyePipeline(fps=args.fps)
    target = args.file if args.mode == "replay" else args.device
    pipeline.add_source(
        source=args.source,
        mode=args.mode,
        target=target,
        use_mock_tracker=args.mock_tracker,
        calibration_file=args.calibration,
    )
    payload = None
    for _ in range(max(1, args.steps)):
        payload = pipeline.step(args.source)

    if args.save_marked and payload is not None:
        out_img = Path(args.save_marked)
        out_img.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_img), pipeline.state.get_marked_frame(args.source))

    if payload is not None:
        print(json.dumps(payload.model_dump(), indent=2, ensure_ascii=False))
    return 0


def _cmd_generate_replay(args: argparse.Namespace) -> int:
    out = generate_sample_video(
        output_path=args.output,
        num_frames=args.frames,
        fps=args.fps,
        source=args.source,
    )
    print(f"Video di replay generato in: {out}")
    return 0


def _cmd_calibrate_arm(args: argparse.Namespace) -> int:
    if args.generate_board:  # foglio A4 orizzontale a 300 dpi da stampare: non calibra niente
        dpi = 300
        board_img, centers_px = render_aruco_workspace(width=3508, height=2480, marker_size_px=600, margin_px=150)
        board_path = Path(args.generate_board)
        board_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(board_path), board_img)
        print(f"Foglio ArUco (A4, stampa al 100%) in: {board_path}. Centri dei marker sul foglio, in mm:")
        for m_id, (cx, cy) in sorted(centers_px.items()):
            print(f"  ID {m_id}: x={cx / dpi * 25.4:.1f}  y={cy / dpi * 25.4:.1f}")
        print(f"Misurate dove finiscono rispetto al braccio e scrivetelo in {args.markers}")
        return 0

    markers = load_marker_coords(args.markers)
    calibrator = ArmCalibrator()
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"Errore: impossibile leggere l'immagine {args.image}")
            return 1
        ok, centers = calibrator.calibrate_from_aruco(frame, markers)
    else:  # camera dal vivo: la RealSense se c'è; si riprova per qualche secondo (esposizione, riflessi)
        mode, target = ("webcam", int(args.device)) if args.device is not None else default_camera(0)
        node = EyePipeline().add_source("arm", mode=mode, target=target, use_mock_tracker=True)
        node.reader.start_background()
        ok, centers, frame = False, {}, None
        try:
            deadline = time.time() + args.seconds
            while not ok and time.time() < deadline:
                time.sleep(0.2)
                _, frame, _, _ = node.reader.get_latest()
                ok, centers = calibrator.calibrate_from_aruco(frame, markers)
        finally:
            node.reader.stop()

    if frame is not None:  # immagine di controllo: dove ha visto i marker
        check = frame.copy()
        for m_id, (cx, cy) in centers.items():
            cv2.circle(check, (int(cx), int(cy)), 8, (0, 255, 0) if m_id in markers else (0, 0, 255), -1)
            cv2.putText(check, str(m_id), (int(cx) + 10, int(cy) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        Path("logs").mkdir(exist_ok=True)
        cv2.imwrite("logs/calibration_check.jpg", check)
    if not ok:
        print(f"Calibrazione fallita: visti i marker {sorted(centers)} (servono 0,1,2,3). "
              "Controllo in logs/calibration_check.jpg")
        return 1

    saved = calibrator.save(args.output)
    w, h = calibrator.image_size
    print(f"Calibrazione completata a {w}x{h} (marker {sorted(centers)}). Salvata in: {saved}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    pipeline = EyePipeline(fps=args.fps)
    mode, arm_target = args.mode, (args.file if args.mode == "replay" else args.device)
    if mode == "auto":  # RealSense USB-C se c'è, se no la webcam del PC
        mode, arm_target = default_camera(int(args.device) if str(args.device).isdigit() else 0)
    pipeline.add_source(
        source="arm",
        mode=mode,
        target=arm_target,
        use_mock_tracker=args.mock_tracker,
        calibration_file=args.calibration,
    )
    if args.dog_target:  # solo se richiesto: una seconda sorgente sulla stessa camera la aprirebbe due volte
        dog_mode = mode if mode in ("replay", "synthetic") else ("webcam" if args.dog_target.isdigit() else "stream")
        pipeline.add_source(
            source="dog",
            mode=dog_mode,
            target=args.dog_target,
            use_mock_tracker=args.mock_tracker,
        )
    pipeline.start()
    app = create_app(pipeline)
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        pipeline.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vision.eyes.cli", description="CLI per Visione A ('Occhi')")
    sub = parser.add_subparsers(dest="command", required=True)

    # inspect
    p_insp = sub.add_parser("inspect", help="Esegue N cicli di percezione e stampa il JSON")
    p_insp.add_argument("--source", default="arm", choices=["arm", "dog"])
    p_insp.add_argument("--mode", default="synthetic", choices=["webcam", "replay", "stream", "synthetic"])
    p_insp.add_argument("--device", default="0")
    p_insp.add_argument("--file", default="data/replays/sample_arm.mp4")
    p_insp.add_argument("--steps", type=int, default=1)
    p_insp.add_argument("--fps", type=float, default=30.0)
    p_insp.add_argument("--calibration", default="data/calibration/calibration_arm.json")
    p_insp.add_argument("--save-marked", default=None)
    p_insp.add_argument("--mock-tracker", action="store_true", default=None)
    p_insp.set_defaults(func=_cmd_inspect)

    # generate-replay
    p_rep = sub.add_parser("generate-replay", help="Genera un file video MP4 sintetico per la modalità replay")
    p_rep.add_argument("--output", default="data/replays/sample_arm.mp4")
    p_rep.add_argument("--frames", type=int, default=60)
    p_rep.add_argument("--fps", type=int, default=30)
    p_rep.add_argument("--source", default="arm")
    p_rep.set_defaults(func=_cmd_generate_replay)

    # calibrate-arm
    p_cal = sub.add_parser("calibrate-arm", help="Calibra l'omografia ArUco del braccio")
    p_cal.add_argument("--device", default=None, help="Indice webcam; senza: RealSense USB-C se c'è, se no webcam 0")
    p_cal.add_argument("--image", default=None, help="Immagine con i 4 marker ArUco")
    p_cal.add_argument("--generate-board", default=None, help="Salva un foglio A4 con i marker 0-3 da stampare ed esce")
    p_cal.add_argument("--markers", default="data/calibration/markers_mm.json",
                       help="Posizioni reali dei marker in mm nel sistema del braccio")
    p_cal.add_argument("--seconds", type=float, default=5.0, help="Per quanto riprovare dalla camera")
    p_cal.add_argument("--output", default="data/calibration/calibration_arm.json")
    p_cal.set_defaults(func=_cmd_calibrate_arm)

    # run
    p_run = sub.add_parser("run", help="Avvia la pipeline e il server FastAPI (MJPEG + WebSocket)")
    p_run.add_argument("--mode", default="auto", choices=["auto", "realsense", "webcam", "replay", "stream", "synthetic"],
                       help="auto = RealSense USB-C se c'è, se no la webcam del PC (--device)")
    p_run.add_argument("--device", default="0", help="Indice webcam; con --mode realsense il seriale (vuoto = la prima)")
    p_run.add_argument("--file", default="data/replays/sample_arm.mp4")
    p_run.add_argument("--dog-target", default=None, help="Camera del cane (indice o stream). Senza: solo 'arm'")
    p_run.add_argument("--calibration", default="data/calibration/calibration_arm.json")
    p_run.add_argument("--fps", type=float, default=25.0)
    p_run.add_argument("--host", default="0.0.0.0")
    p_run.add_argument("--port", type=int, default=8000)
    p_run.add_argument("--mock-tracker", action="store_true", default=None)
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
