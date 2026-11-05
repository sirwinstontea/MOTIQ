"""Command line for robot-chooser (see __main__.py for usage)."""
from __future__ import annotations

import argparse
import json
import sys

import yaml

from .core import Chooser, ChooserError


def _nested(dotted: str, value) -> dict:
    out = value
    for part in reversed(dotted.split(".")):
        out = {part: out}
    return out


def _print_report(state: dict) -> None:
    rep = state["report"]
    print(f"{rep['robot'] or rep['robot_id']} | {rep['backend']} | {rep['dof']} DOF -> {rep['status']}")
    if rep.get("payload_budget_kg") is not None:
        print(f"  payload budget {rep['payload_budget_kg']} kg, reach {rep['reach_m']} m + TCP {rep['tcp_length_m']} m")
    for title, key in (("BLOCKERS", "blockers"), ("warnings", "warnings"), ("notes", "notes")):
        if rep[key]:
            print(f"  {title}:")
            for i in rep[key]:
                print(f"    [{i['code']}] {i['message']}")
    sel = state.get("selection") or {}
    if sel.get("files"):
        print("  generated: " + ", ".join(sel["files"]))


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="robot_chooser")
    ap.add_argument("--root", default=None, help="MOTIQ repo root (default: parent of robot-chooser/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--brand")
    ls.add_argument("--dof", type=int)
    ls.add_argument("--text", default="")
    for name in ("status", "apply", "json"):
        sub.add_parser(name)
    sub.add_parser("select").add_argument("robot_id")
    st = sub.add_parser("set")
    st.add_argument("key")
    st.add_argument("value")
    sr = sub.add_parser("set-robot")
    for a in ("robot_id", "key", "value"):
        sr.add_argument(a)
    ar = sub.add_parser("add-robot", help="add any arm to the catalog (starts blocked until configured)")
    ar.add_argument("--id", required=True, help="lowercase id, e.g. kuka_kr3_r540")
    ar.add_argument("--brand", required=True)
    ar.add_argument("--model", required=True)
    ar.add_argument("--dof", type=int, required=True)
    ar.add_argument("--reach", type=float, required=True, help="nominal reach in m")
    ar.add_argument("--payload", type=float, required=True, help="nominal payload in kg")
    ar.add_argument("--url", required=True, help="https:// RoboDK library or manufacturer page")
    ar.add_argument("--robot-file", default=None, help="<name>.robot in robot_controller/stations/")
    sub.add_parser("remove-robot").add_argument("robot_id")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8770)
    return ap


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    ch = Chooser(args.root) if args.root else Chooser()
    try:
        if args.cmd == "list":
            keep = set(ch.filter_catalog(args.text, args.brand, args.dof))
            for row in ch.state()["catalog"]:
                if row["id"] in keep:
                    mark = "*" if row["active"] else " "
                    print(f"{mark} {row['id']:<15} {row['brand']:<17} {row['model']:<6} {row['dof']} DOF  "
                          f"reach {row['nominal_reach_m']:.3f} m  payload {row['nominal_payload_kg']:g} kg  "
                          f"[{row['status']}]  {row['library_url']}")
            return 0
        if args.cmd == "serve":
            from .server import serve
            serve(ch, args.host, args.port)
            return 0
        if args.cmd == "json":
            print(json.dumps(ch.state(), indent=2))
            return 0
        if args.cmd == "select":
            state = ch.select(args.robot_id)
        elif args.cmd == "set":
            state = ch.update_hardware(_nested(args.key, yaml.safe_load(args.value)))
        elif args.cmd == "set-robot":
            state = ch.update_robot(args.robot_id, _nested(args.key, yaml.safe_load(args.value)))
        elif args.cmd == "add-robot":
            state = ch.add_robot({"id": args.id, "brand": args.brand, "model": args.model, "dof": args.dof,
                                  "nominal_reach_m": args.reach, "nominal_payload_kg": args.payload,
                                  "library_url": args.url, "library_file": args.robot_file})
            print(f"added {args.id} (blocked until limits, scene and workspace are set)")
        elif args.cmd == "remove-robot":
            state = ch.remove_robot(args.robot_id)
        elif args.cmd == "apply":
            state = ch.apply()
        else:
            state = ch.state()
        _print_report(state)
        return 0 if state["report"]["status"] == "READY" else 2
    except ChooserError as e:
        print(f"error [{e.code}]: {e.message}", file=sys.stderr)
        return 1
