"""Riga di comando (CLI) per Visione A ("Occhi").

Esempi d'uso:
  python -m vision.eyes.cli inspect --mode synthetic
  python -m vision.eyes.cli generate-replay --output data/replays/sample_arm.mp4
  python -m vision.eyes.cli calibrate-arm --generate-board logs/aruco_a4.png   # foglio da stampare
  python -m vision.eyes.cli calibrate-arm      # dalla camera (RealSense se c'è), marker in data/calibration/markers_mm.json
  python -m vision.eyes.cli run --port 8000                  # RealSense USB-C, se no webcam del PC
  python -m vision.eyes.cli run --mode synthetic --port 8000
  python -m vision.eyes.cli calibrate-look-pose --table-z-mm 0   # camera sul braccio, posa fissa (consigliato)
  python -m vision.eyes.cli calibrate-look-pose --check          # il braccio è tornato nella stessa posa?
  python -m vision.eyes.cli calibrate-hand-eye             # camera sul braccio: foglio ArUco + posa TCP dal controller
  python -m vision.eyes.cli calibrate-hand-eye --tcp manual   # posa TCP scritta a mano dal pendant
  python -m vision.eyes.cli run --look-pose "-450,-200,400,3.1416,0,0"   # posa TCP fissa dal pendant (mm, rad)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import uvicorn

import numpy as np

from .calibration.aruco_calib import ArmCalibrator, load_marker_coords, render_aruco_workspace
from .calibration.hand_eye import (
    ControllerTcp, HandEye, RtdeTcp, board_pose, solve_and_save, board_spread_mm, calibrate, calibrate_fixed, camera_matrix, parse_pose_mm,
    markers_still_on_sheet, table_footprint, table_height_error_mm,
)
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


def _camera_mount(args: argparse.Namespace) -> dict:
    opts = {}
    if getattr(args, "mount", None):
        opts["mount"] = parse_pose_mm(args.mount)
    return opts


def _color_labels(text: str | None) -> dict[str, str] | None:
    """`"arancione=vite_arancione,blu=secchio_blu"` -> dict."""
    if not text:
        return None
    return dict(pair.split("=", 1) for pair in text.replace(" ", "").split(",") if "=" in pair)


def _arm_camera(args: argparse.Namespace):
    """Sorgente `arm` per le calibrazioni: la RealSense."""
    return EyePipeline().add_source("arm", mode="realsense", target=args.device or 0, use_mock_tracker=True)


def _cmd_calibrate_look_pose(args: argparse.Namespace) -> int:
    """Posa fissa: una foto del foglio ArUco (centri misurati nel base) dalla posa di osservazione -> T_base_cam."""
    markers = load_marker_coords(args.markers)
    if markers_still_on_sheet(markers):
        print(f"[ERRORE] {args.markers} contiene ancora le posizioni dei marker SUL FOGLIO, non nel frame Base del robot.\n"
              "Toccate il centro di ogni marker con il TCP (pendant, riferimento Base) e scrivete X, Y in mm:\n"
              '  {"0": [x, y], "1": [x, y], "2": [x, y], "3": [x, y]}')
        return 1
    if args.table_z_mm is None:  # scritta da calibrate-hand-eye insieme ai centri dei marker, se c'è
        args.table_z_mm = float(json.loads(Path(args.markers).read_text(encoding="utf-8")).get("_table_z_mm") or 0.0)
        print(f"Quota del tavolo da {args.markers}: {args.table_z_mm:.1f} mm")
    table_z = args.table_z_mm / 1000.0
    node = _arm_camera(args)
    node.reader.start_background()
    intr = node.reader.intrinsics
    best, frame, depth = None, None, None
    try:
        if intr is None:
            print("Serve la camera con profondità (RealSense o PyBullet): non risponde.")
            return 1
        deadline = time.time() + args.seconds
        tries = 0
        while time.time() < deadline and tries < 15:  # più foto: si tiene quella con l'errore più basso
            time.sleep(0.15)
            _, f, d, _, _ = node.reader.get_frames()
            res = calibrate_fixed(f, intr, markers, table_z)
            if res is not None:
                tries += 1
                if best is None or res[2] < best[2]:
                    best, frame, depth = res, f, d
    finally:
        node.reader.stop()
    if best is None:
        print(f"Foglio non visto: servono almeno 3 dei marker {sorted(markers)} nell'inquadratura")
        return 1

    T, n, err = best
    table_mm = table_height_error_mm(depth, intr, T, table_z)
    cam = T[:3, 3] * 1000.0
    print(f"Marker visti: {n}  |  errore di riproiezione: {err:.2f} px (bene se < 1)")
    print(f"Camera nel frame base: x={cam[0]:.1f}  y={cam[1]:.1f}  z={cam[2]:.1f} mm")
    if table_mm is not None:
        print(f"Controllo profondità: il tavolo risulta a {table_mm:+.1f} mm dalla quota attesa (bene se entro ±5)")
    fp = table_footprint(T, intr, table_z)
    if fp is not None:
        lo, hi = fp.min(axis=0) * 1000, fp.max(axis=0) * 1000
        print(f"Da questa posa la camera vede il tavolo da X {lo[0]:.0f} a {hi[0]:.0f} mm e da Y {lo[1]:.0f} a "
              f"{hi[1]:.0f} mm: vite e secchio devono stare lì dentro, lontano dai bordi")

    check = frame.copy()  # assi del frame base disegnati sull'immagine: l'origine deve cadere dove ce l'aspettiamo
    T_cam_base = np.linalg.inv(T)
    cv2.drawFrameAxes(check, camera_matrix(intr), np.zeros(5), cv2.Rodrigues(T_cam_base[:3, :3])[0],
                      T_cam_base[:3, 3], 0.1)
    Path("logs").mkdir(exist_ok=True)
    cv2.imwrite("logs/look_pose_check.jpg", check)
    print("Immagine di controllo (assi X rosso, Y verde, Z blu del base): logs/look_pose_check.jpg")

    if args.check:  # solo confronto con la calibrazione salvata: il braccio è nella stessa posa? la camera si è mossa?
        old = HandEye.load(args.output).T_tool_cam
        dt = np.linalg.norm(old[:3, 3] - T[:3, 3]) * 1000.0
        dr = np.degrees(np.arccos(np.clip((np.trace(old[:3, :3].T @ T[:3, :3]) - 1) / 2, -1, 1)))
        print(f"Rispetto a {args.output}: camera spostata di {dt:.1f} mm e {dr:.2f}°"
              + ("  -> OK" if dt < 3 and dr < 0.5 else "  -> RICALIBRARE (o il braccio non è nella posa giusta)"))
        return 0 if dt < 3 and dr < 0.5 else 1

    bad = err > 2.0 or (table_mm is not None and abs(table_mm) > 10.0)
    if bad:
        print("Risultato poco affidabile: controllate le misure dei marker in", args.markers, "e la quota del tavolo")
    he = HandEye(T_tool_cam=T, method="fixed_pnp", n_samples=n, tcp_source="fixed",
                 image_size=(intr.width, intr.height),
                 extra={"table_z_m": table_z, "reproj_px": round(err, 2),
                        "table_check_mm": round(table_mm, 1) if table_mm is not None else None,
                        "markers_base_mm": {str(k): list(v) for k, v in markers.items()}})
    print(f"Salvata in: {he.save(args.output)}")
    return 1 if bad else 0


def _cmd_calibrate_hand_eye(args: argparse.Namespace) -> int:
    """Raccoglie coppie (posa TCP, posa del foglio ArUco) dalla RealSense sul braccio e calcola `T_tool_cam`."""
    if args.tcp == "manual":
        tcp_fn = None
    elif args.tcp == "rtde":
        if not args.robot_ip:
            print("Con --tcp rtde serve --robot-ip (l'IP dell'UR, lo trovate sul pendant: Impostazioni > Rete)")
            return 1
        tcp_fn = RtdeTcp(args.robot_ip)
    else:
        tcp_fn = ControllerTcp(args.controller, timeout_s=1.0, backoff_s=0.0)
    node = _arm_camera(args)
    node.reader.start_background()
    intr = node.reader.intrinsics
    if intr is None:
        print("Serve la camera con profondità (RealSense o PyBullet): non risponde.")
        node.reader.stop()
        return 1
    poses, boards = [], []
    print("Foglio ArUco fermo sul tavolo. Per ogni posa del braccio (12-15, ruotate su almeno 2 assi): fermalo, poi\n"
          "  SPAZIO = prendi il campione   C = calcola e salva   Q = esci senza salvare")
    try:
        while True:
            _, frame, _, _ = node.reader.get_latest()
            found = board_pose(frame, intr)
            view = frame.copy()
            msg = f"campioni {len(poses)}  " + (f"foglio OK ({found[1]} marker, {found[2]:.2f} px)" if found
                                                 else "foglio non visto (servono 2+ marker)")
            if found:
                cv2.drawFrameAxes(view, camera_matrix(intr), np.zeros(5), cv2.Rodrigues(found[0][:3, :3])[0],
                                  found[0][:3, 3], 0.05)
            cv2.putText(view, msg, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0) if found else (0, 0, 255), 2)
            cv2.imshow("calibrate-hand-eye", view)
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q"):
                return 1
            if key == ord(" ") and found:
                if tcp_fn is None:
                    try:
                        pose = parse_pose_mm(input("Posa TCP dal pendant (x y z in mm, rx ry rz in rad): "))
                    except ValueError as exc:
                        print(f"  scartato: {exc}")
                        continue
                else:
                    r = tcp_fn()
                    if r is None or r.moving:
                        print(f"  scartato: posa TCP non disponibile o robot in moto ({tcp_fn.last_error or 'busy'})")
                        continue
                    pose = list(r.pose)
                poses.append(pose)
                boards.append(found[0])
                print(f"  campione {len(poses)}: TCP {[round(v, 4) for v in pose]}")
            if key == ord("c"):
                if len(poses) < 3:
                    print("  servono almeno 3 campioni (meglio 12-15)")
                    continue
                break
    finally:
        cv2.destroyAllWindows()
        node.reader.stop()

    if len(poses) < 8:
        print(f"Attenzione: solo {len(poses)} campioni, il risultato può essere instabile")
    res = solve_and_save(poses, boards, intr, args.tcp, args.output, args.markers_out or None, args.method)
    print(f"Camera nel TCP: {res['camera_in_tool_mm']} mm")
    print(f"Dispersione del foglio nel frame base: {res['spread_mm']:.1f} mm (bene se < 5)")
    if not res["reliable"]:
        print("Risultato poco affidabile: più pose, rotazioni più ampie, foglio ben piatto e fermo")
    print(f"Salvata in: {res['hand_eye_file']}")
    if "markers_file" in res:  # il foglio non si è mosso: i suoi marker nel base escono dalla hand-eye, senza toccarli
        print(f"Centri dei marker nel base scritti in {res['markers_file']}: {res['markers_base_mm']}, "
              f"quota del foglio {res['table_z_mm']:.1f} mm")
        print("Adesso, SENZA spostare il foglio: braccio nella posa di osservazione, poi calibra_braccio.bat")
    return 0


def _flange_provider(args: argparse.Namespace):
    """Read-only flange pose source for Skills -> Calibration: physical UR via RTDE receive, or a labelled simulation."""
    from .calibration.setup_config import load_setup
    from .capture.ur_flange import MockFlangeProvider, RtdeFlangeProvider, pose_to_T as ur_pose_to_T

    def setup_fn():
        return load_setup()[0]

    if getattr(args, "simulate_robot", None):
        T = ur_pose_to_T(parse_pose_mm(args.simulate_robot))
        print("[Robot] SIMULATION flange pose (set robot.pose_mode='simulation' in calibration_setup.json)")
        return MockFlangeProvider(lambda: T, setup_fn)
    if getattr(args, "robot_ip", None):
        print(f"[Robot] read-only flange telemetry from UR at {args.robot_ip} (rtde_receive; never moves the robot)")
        return RtdeFlangeProvider(args.robot_ip, setup_fn)
    return None


def _cmd_flange_pose(args: argparse.Namespace) -> int:
    """Phase A check: print verified T_base_flange readings from the real UR (read-only), a few times."""
    import time as _time

    import numpy as _np

    from .capture.ur_flange import pose_delta
    prov = _flange_provider(args)
    if prov is None:
        print("Needs --robot-ip <UR IP>")
        return 2
    first = None
    for k in range(args.count):
        r = prov.read()
        if not r.valid:
            print(f"[{k + 1}] INVALID ({r.status}): {r.reason}")
        else:
            d = "" if first is None else "  drift vs first: %.2f mm / %.3f deg" % pose_delta(first, r.T_base_flange)
            first = first if first is not None else r.T_base_flange
            print(f"[{k + 1}] {r.source} moving={r.moving} xyz_m={_np.round(r.T_base_flange[:3, 3], 5).tolist()} "
                  f"check={r.diagnostics.get('fk_crosscheck_mm', '-')} mm{d}")
        _time.sleep(args.interval)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    pipeline = EyePipeline(fps=args.fps)
    mode, arm_target = args.mode, (args.file if args.mode == "replay" else args.device)
    if mode == "auto":  # RealSense USB-C se c'è, se no la webcam del PC
        mode, arm_target = default_camera(int(args.device) if str(args.device).isdigit() else 0)
    arm = pipeline.add_source(
        source="arm",
        mode=mode,
        target=arm_target,
        use_mock_tracker=args.mock_tracker,
        calibration_file=args.calibration,
        fixed_pose_file=args.fixed_pose,
        hand_eye_file=args.hand_eye,
        controller_url=args.controller,
        look_pose=parse_pose_mm(args.look_pose) if args.look_pose else None,
        robot_pose=args.robot_pose,
        color_labels=_color_labels(args.color_labels),
        sim_options=_camera_mount(args) if mode == "pybullet" else None,
    )
    if args.robot_ip:  # sola lettura della posa: serve alla calibrazione dall'app (skill), non muove mai il robot
        try:
            arm.robot_pose_fn = RtdeTcp(args.robot_ip)   # legacy TCP-pose path (/calibration/arm/sample)
        except RuntimeError as exc:
            print(f"[Robot] legacy TCP reader unavailable: {exc}")
    arm.flange_provider = _flange_provider(args)     # Skills -> Calibration: verified flange pose, read-only
    if mode == "realsense" and getattr(args, "include_webcam", True):
        try:
            pipeline.add_source(
                source="webcam",
                mode="webcam",
                target=int(args.webcam_device) if str(args.webcam_device).isdigit() else 0,
                use_mock_tracker=args.mock_tracker,
            )
            print(f"[Pipeline] Sorgente 'webcam' (PC webcam {args.webcam_device}) aggiunta con doppio modello attivo!")
        except Exception as exc:
            print(f"[Pipeline] Avviso: impossibile avviare sorgente 'webcam': {exc}")
    pipeline.start()
    app = create_app(pipeline)
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        pipeline.stop()
    return 0


def _add_mount_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mount", default="0,-70,30,0,0,0",
                   help="Camera nella flangia 'x,y,z,rx,ry,rz' (mm, rad)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vision.eyes.cli", description="CLI per Visione A ('Occhi')")
    sub = parser.add_subparsers(dest="command", required=True)

    # inspect
    p_insp = sub.add_parser("inspect", help="Esegue N cicli di percezione e stampa il JSON")
    p_insp.add_argument("--source", default="arm", choices=["arm"])
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
    p_run.add_argument("--mode", default="auto",
                       choices=["auto", "realsense", "pybullet", "webcam", "replay", "stream", "synthetic"],
                       help="auto = RealSense USB-C se c'è, se no la webcam del PC (--device)")
    p_run.add_argument("--device", default="0", help="Indice webcam; con --mode realsense il seriale (vuoto = la prima)")
    p_run.add_argument("--file", default="data/replays/sample_arm.mp4")
    p_run.add_argument("--calibration", default="data/calibration/calibration_arm.json")
    p_run.add_argument("--fps", type=float, default=25.0)
    p_run.add_argument("--host", default="0.0.0.0")
    p_run.add_argument("--port", type=int, default=8000)
    p_run.add_argument("--mock-tracker", action="store_true", default=None)
    p_run.add_argument("--fixed-pose", default="data/calibration/fixed_pose.json",
                       help="Calibrazione a posa fissa (calibrate-look-pose); se il file c'è, ha la precedenza")
    p_run.add_argument("--hand-eye", default="data/calibration/hand_eye.json",
                       help="Calibrazione della camera sul braccio; se il file c'è, oggetti anche nel frame base")
    p_run.add_argument("--controller", default="http://127.0.0.1:8765",
                       help="robot_controller da cui leggere la posa TCP (GET /robot/state, sola lettura)")
    p_run.add_argument("--look-pose", default=None,
                       help="Posa TCP fissa dal pendant 'x,y,z,rx,ry,rz' (mm, rad) al posto del controller")
    p_run.add_argument("--robot-ip", default=None,
                       help="IP dell'UR: la posa si legge via RTDE (sola lettura) per calibrare dall'app")
    p_run.add_argument("--simulate-robot", default=None, metavar="X,Y,Z,RX,RY,RZ",
                       help="Demo only: fixed SIMULATED flange pose (mm, rad) for Skills -> Calibration")
    p_run.add_argument("--robot-pose", default="controller", choices=["controller"],
                       help="Con la hand-eye: posa TCP da robot_controller")
    p_run.add_argument("--color-labels", default=None,
                       help="Con --mock-tracker: 'arancione=vite_arancione,blu=secchio_blu' (oggetti per colore)")
    p_run.add_argument("--include-webcam", action="store_true", default=True,
                       help="Include anche la webcam normale del PC come sorgente 'webcam'")
    p_run.add_argument("--no-webcam", dest="include_webcam", action="store_false",
                       help="Disabilita l'inclusione automatica della webcam del PC")
    p_run.add_argument("--webcam-device", default="0",
                       help="Indice webcam del PC per la sorgente 'webcam' (default: 0)")
    _add_mount_arg(p_run)
    p_run.set_defaults(func=_cmd_run)

    # flange-pose (read-only): Phase A acceptance check for Skills -> Calibration
    p_fp = sub.add_parser("flange-pose", help="Print verified UR5e T_base_flange readings (read-only RTDE)")
    p_fp.add_argument("--robot-ip", required=True)
    p_fp.add_argument("--count", type=int, default=5)
    p_fp.add_argument("--interval", type=float, default=1.0)
    p_fp.set_defaults(func=_cmd_flange_pose)

    # calibrate-look-pose
    p_lp = sub.add_parser("calibrate-look-pose",
                          help="Camera sul braccio, posa fissa: una foto del foglio ArUco dalla posa di osservazione")
    p_lp.add_argument("--device", default=None, help="Seriale della RealSense (vuoto = la prima)")
    p_lp.add_argument("--markers", default="data/calibration/markers_mm.json",
                      help="Centri dei marker 0-3 in mm nel frame BASE del robot (misurati col TCP)")
    p_lp.add_argument("--table-z-mm", type=float, default=None,
                      help="Quota del piano del tavolo nel frame base, mm (default: _table_z_mm del file dei marker, o 0)")
    p_lp.add_argument("--seconds", type=float, default=5.0)
    p_lp.add_argument("--check", action="store_true",
                      help="Non salva: confronta con la calibrazione salvata (braccio nella posa giusta?)")
    p_lp.add_argument("--output", default="data/calibration/fixed_pose.json")
    p_lp.add_argument("--camera", default="realsense", choices=["realsense"])
    p_lp.set_defaults(func=_cmd_calibrate_look_pose)

    # calibrate-hand-eye
    p_he = sub.add_parser("calibrate-hand-eye", help="Calibra la camera sul braccio (T_tool_cam) col foglio ArUco")
    p_he.add_argument("--device", default=None, help="Seriale della RealSense (vuoto = la prima)")
    p_he.add_argument("--tcp", default="controller", choices=["controller", "manual", "rtde"],
                      help="Posa dal controller (GET /robot/state) o scritta a mano dal pendant")
    p_he.add_argument("--camera", default="realsense", choices=["realsense"])
    p_he.add_argument("--controller", default="http://127.0.0.1:8765")
    p_he.add_argument("--method", default="park", choices=["park", "tsai", "horaud", "daniilidis"])
    p_he.add_argument("--output", default="data/calibration/hand_eye.json")
    p_he.add_argument("--robot-ip", default=None, help="IP dell'UR per --tcp rtde")
    p_he.add_argument("--markers-out", default="data/calibration/markers_mm.json",
                      help="Dove scrivere i centri dei marker nel base calcolati ('' = non scriverli)")
    p_he.set_defaults(func=_cmd_calibrate_hand_eye)
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
