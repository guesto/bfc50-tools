"""Command line interface. SPDX-License-Identifier: GPL-3.0-or-later."""
import argparse
from datetime import datetime, timezone
from importlib.resources import files
import json
from pathlib import Path
import sys

from . import __version__
from .memory import (RADIO_ID, build_image, describe_image, load_plan, load_profile,
                     save_plan, sha256)
from .protocol import RadioError, connect, program


def default_profile():
    return files("bfc50_tools").joinpath("profiles/pmr446-8.csv")


def parser():
    root = argparse.ArgumentParser(description="Experimental BF-C50 channel tools")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("ports", help="List serial ports (does not open them)")
    command = commands.add_parser("init-profile", help="Copy the bundled 8-channel CTCSS-OFF profile")
    command.add_argument("--output", type=Path, required=True)
    for name, help_text in (("probe", "Identify a radio; no channel writes"),
                            ("read", "Save the radio's own backup"),
                            ("write", "Apply a reviewed plan; requires --apply"),
                            ("verify", "Compare radio memory with a prepared plan")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--port", required=True, help="e.g. /dev/ttyUSB0 or COM3")
        command.add_argument("--log-dir", type=Path, default=Path("logs"))
        if name == "read":
            command.add_argument("--output", type=Path, required=True)
        if name in {"write", "verify"}:
            command.add_argument("--plan", type=Path, required=True)
        if name == "write":
            command.add_argument("--apply", action="store_true", help="Actually write channel records")
    command = commands.add_parser("inspect", help="Decode a local backup without opening a port")
    command.add_argument("--image", type=Path, required=True)
    command = commands.add_parser("plan", help="Build a reviewable plan offline")
    command.add_argument("--baseline", type=Path, required=True)
    command.add_argument("--profile", type=Path, help="Default: bundled PMR446 channels 1..8, tones OFF")
    command.add_argument("--output", type=Path, required=True, help="New output prefix, e.g. plans/radio1")
    command.add_argument("--unused", choices=("disable", "preserve"), default="disable",
                         help="Unlisted channel policy (default: disable)")
    return root


def print_plan(metadata):
    print(f"Baseline SHA256: {metadata['baseline_sha256']}")
    print(f"Target SHA256:   {metadata['target_sha256']}")
    print("Changed channels:", ", ".join(str(address // 16 + 1) for address in metadata["changed_blocks"]) or "none")
    print(json.dumps(metadata["channels"], indent=2, ensure_ascii=False))


def hardware(args, loaded=None):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    args.log_dir.mkdir(parents=True, exist_ok=True)
    base = args.log_dir / f"{args.command}-{stamp}"
    record = {"utc": stamp, "command": args.command, "port": args.port, "events": []}

    def snapshot(label, data):
        path = Path(f"{base}-{label}.bin")
        with path.open("xb") as stream:
            stream.write(data)
        record[f"{label}_image"] = str(path)

    try:
        with connect(args.port, record["events"]) as protocol:
            record["radio_id"] = RADIO_ID.hex()
            if args.command == "probe":
                record["result"] = "identified"
                print(f"ID: {RADIO_ID.hex(' ')}; no channel memory written")
            elif args.command == "read":
                data = protocol.read_image()
                with args.output.open("xb") as stream:
                    stream.write(data)
                metadata = {"format": "bfc50-backup-v1", "utc": stamp,
                            "radio_id": RADIO_ID.hex(), "size": len(data), "sha256": sha256(data)}
                with args.output.with_suffix(".json").open("x", encoding="utf-8") as stream:
                    json.dump(metadata, stream, indent=2)
                    stream.write("\n")
                record.update(result="complete", **metadata)
                print(f"Backup: {args.output} ({len(data)} bytes)")
                print(f"SHA256: {sha256(data)}")
            elif args.command == "write":
                metadata, baseline, target = loaded
                record["plan_sha256"] = sha256(args.plan.read_bytes())
                record["baseline_sha256"] = metadata["baseline_sha256"]
                program(protocol, baseline, target, record, snapshot)
                print(f"{record['result'].upper()}: full memory matches the plan")
            elif args.command == "verify":
                metadata, _, target = loaded
                actual = protocol.read_image()
                snapshot("readback", actual)
                record["channels_match"] = actual[:0x100] == target[:0x100]
                record["mismatches"] = [i for i, (a, b) in enumerate(zip(actual, target)) if a != b]
                if actual != target:
                    raise RadioError("Full-image verification failed; see mismatches and channels_match in the log")
                record.update(result="verified", readback_sha256=sha256(actual))
                print("VERIFIED: all 1632 bytes match the plan")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        record.update(result="failed", error=str(exc) or "Interrupted")
        print(f"ERROR: {record['error']}", file=sys.stderr)
        if "rollback" in record:
            print(f"Rollback: {record['rollback']}", file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        log = Path(f"{base}.json")
        with log.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        print(f"Log: {log}")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "ports":
            from serial.tools import list_ports
            for port in list_ports.comports():
                print(f"{port.device}\t{port.description}\t{port.hwid}")
            return 0
        if args.command == "init-profile":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as stream:
                stream.write(default_profile().read_bytes())
            print(args.output)
            return 0
        if args.command == "inspect":
            print(json.dumps(describe_image(args.image.read_bytes()), indent=2, ensure_ascii=False))
            return 0
        if args.command == "plan":
            baseline = args.baseline.read_bytes()
            channels = load_profile(args.profile or default_profile())
            target = build_image(baseline, channels, unused=args.unused)
            path, metadata = save_plan(baseline, target, args.output, args.unused)
            print_plan(metadata)
            print(f"Plan: {path}; offline preparation, serial port not opened")
            return 0
        loaded = None
        if args.command in {"write", "verify"}:
            loaded = load_plan(args.plan)
        if args.command == "write" and not args.apply:
            print_plan(loaded[0])
            print("DRY RUN: serial port not opened; add --apply to write")
            return 0
        if args.command == "read":
            if args.output.suffix.lower() != ".bin":
                raise ValueError("Backup output must have a .bin extension")
            if args.output.exists() or args.output.with_suffix(".json").exists():
                raise ValueError("Backup output already exists; choose a new filename")
            args.output.parent.mkdir(parents=True, exist_ok=True)
        return hardware(args, loaded)
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
