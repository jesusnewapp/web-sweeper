from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .engines import run_selected
from .discovery import DEFAULT_CATEGORIES, discover
from .dock import cleanup_verified_staging, promote, staged, validate_attestation
from .state import State
from .translation import capabilities, translate_file
from .translation_fleet import TranslationFleet
from .continuation import build_plan
from .enforcer import enforce
from .activity import report as activity_report
from .activity import record as activity_record
from .tertiary import adapter_view, inquisitive_read, observe as tertiary_observe
from .bridge import decision as bridge_decision


EXAMPLE = {
    "workspace": "./sweeper-data",
    "user_agent": "YOUR INSTITUTION Sweeper V2/0.1 (YOUR-CONTACT@example.org)",
    "project": {"name": "My Collection", "overall_target_items": 0,
                "daily_target_items": 0},
    "engine": {"mode": "ultra"},
    "tertiary": {"enabled": False, "inquisitive_enabled": False,
        "adapter_enabled": False, "signals": ["nurture", "pivot", "continuation"]},
    "bridge": {"enabled": False, "nurture_threshold_percent": 50.0},
    "layout": {"major_slots": 2, "minor_slots": 2},
    "policy": {
        "languages": ["en"], "licenses": ["PUBLIC-DOMAIN", "CC0-1.0", "CC-BY-4.0"],
        "media_types": ["text/*", "audio/*", "video/*", "image/*", "application/json",
                        "application/xml", "application/zip", "application/epub+zip",
                        "application/vnd.comicbook+zip", "application/vnd.comicbook-rar"],
        "artifact_classes": ["document", "dataset", "archive", "audio", "music", "video",
                             "image", "comic", "software", "map", "model", "other"],
        "data_classes": ["open-public", "institution-authorized"],
        "minimum_bytes": 1, "maximum_bytes": 1073741824,
        "require_language": True, "require_license": True, "require_rights_evidence": True,
        "reviewer_command": [],
    },
    "translation": {"enabled": False, "batch_size": 50,
        "staging_collection": "REPLACE_WITH_YOUR_TRANSLATION_STAGING_COLLECTION",
        "target_languages": [], "notifier_command": [],
        "validator_command": [], "stager_command": []},
    "sources": [],
}


def project_file(directory: Path, name: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise SystemExit("project name must contain a letter or number")
    return directory.resolve() / f"{slug}.json"


def save_project(config_path: Path, directory: Path, name: str) -> Path:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw.setdefault("project", {})["name"] = name
    base = config_path.resolve().parent
    workspace = Path(str(raw.get("workspace", "./sweeper-data")))
    if not workspace.is_absolute():
        raw["workspace"] = str((base / workspace).resolve())
    for source in raw.get("sources", []):
        manifest = str(source.get("manifest", ""))
        if manifest and "://" not in manifest and not Path(manifest).is_absolute():
            source["manifest"] = str((base / manifest).resolve())
        source["continuation_manifests"] = [
            location if "://" in str(location) or Path(str(location)).is_absolute()
            else str((base / str(location)).resolve())
            for location in source.get("continuation_manifests", [])
        ]
    target = project_file(directory, name)
    if target.exists():
        raise SystemExit(f"refusing to overwrite saved project: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    load_config(target)
    return target


def load_project(directory: Path, name: str, config_path: Path) -> Path:
    source = project_file(directory, name)
    if not source.exists():
        raise SystemExit(f"saved project not found: {source}")
    if config_path.exists():
        raise SystemExit(f"refusing to overwrite existing configuration: {config_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, config_path)
    load_config(config_path)
    return config_path


def initialize(path: Path) -> None:
    if path.exists():
        raise SystemExit(f"refusing to overwrite existing configuration: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(EXAMPLE, indent=2) + "\n", encoding="utf-8")
    manifests = path.parent / "manifests"
    manifests.mkdir(exist_ok=True)
    template = manifests / "source.example.jsonl"
    template.write_text(
        json.dumps({"id": "stable-record-id", "url": "https://example.org/file.xml",
                    "title": "Example record", "language": "en", "license": "CC0-1.0",
                    "rights_evidence_url": "https://example.org/rights",
                    "media_type": "application/xml", "artifact_class": "document",
                    "data_class": "open-public", "metadata": {"topic": "your topic"}}) + "\n",
        encoding="utf-8",
    )


def daemon(config_path: Path, interval: float, once: bool = False,
           max_backoff: float = 900.0) -> int:
    config = load_config(config_path)
    state_path = config.workspace / "daemon-state.json"
    consecutive_failures = 0
    def write_health(value: dict) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.replace(state_path)

    while True:
        payload = {"mode": "always-running", "checkedAt": datetime.now(timezone.utc).isoformat()}
        try:
            def heartbeat(detail: dict) -> None:
                write_health({"mode": "always-running", "status": "working",
                    "checkedAt": datetime.now(timezone.utc).isoformat(), "progress": detail,
                    "consecutiveFailures": consecutive_failures})
            result = run_selected(config, progress=heartbeat)
            pivot = enforce(config)
            activity_record(config.workspace,"ten-minute-pivot-evaluation",lane="pivot-enforcer",
                status="action-required" if pivot.get("enforcementRequired") else "progressing",
                detail={"enforcementRequired":pivot.get("enforcementRequired"),
                        "overdue":pivot.get("overdue",[])})
            payload.update({"status": "degraded" if result.get("sourceErrors") else "healthy",
                            "result": result,"pivotEvaluation":pivot,"pivotEverySeconds":600})
        except Exception as error:
            payload.update({"status": "retrying", "error": f"{type(error).__name__}: {error}"})
            consecutive_failures += 1
        else:
            consecutive_failures = 0
        next_delay = min(60.0, max_backoff, interval * (2 ** max(0, consecutive_failures - 1)))
        payload.update({"consecutiveFailures": consecutive_failures,
                        "nextCheckSeconds": next_delay,"pivotEverySeconds":600})
        write_health(payload)
        print(json.dumps(payload, indent=2), flush=True)
        if once:
            return 0 if payload["status"] == "healthy" else 1
        time.sleep(next_delay)


def main() -> int:
    parser = argparse.ArgumentParser(prog="sweeper")
    sub = parser.add_subparsers(dest="command", required=True)
    initialize_command = sub.add_parser("init")
    initialize_command.add_argument("--config", type=Path, default=Path("sweeper.json"))
    for name in ("validate", "run", "status", "plan", "sources"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("sweeper.json"))
        if name == "run":
            command.add_argument("--engine", choices=("ultra", "dual", "v2"))
    engine_mode = sub.add_parser("engine-mode")
    engine_mode.add_argument("--config", type=Path, default=Path("sweeper.json"))
    engine_mode.add_argument("--set", dest="selected", choices=("ultra", "dual", "v2"))
    activity_command = sub.add_parser("activity-log")
    activity_command.add_argument("--config", type=Path, default=Path("sweeper.json"))
    activity_command.add_argument("--limit", type=int, default=100)
    tertiary_mode = sub.add_parser("tertiary-mode")
    tertiary_mode.add_argument("--config", type=Path, default=Path("sweeper.json"))
    tertiary_mode.add_argument("--set", dest="selected", choices=("on", "off"))
    tertiary_mode.add_argument("--adapter", choices=("on", "off"))
    tertiary_mode.add_argument("--inquisitive", choices=("on", "off"))
    bridge_switch = sub.add_parser("bridge-switch")
    bridge_switch.add_argument("--config", type=Path, default=Path("sweeper.json"))
    bridge_switch.add_argument("--set", dest="selected", choices=("on", "off"))
    bridge_switch.add_argument("--threshold", type=float)
    bridge_switch.add_argument("--accepted", type=int, default=0)
    bridge_switch.add_argument("--target", type=int, default=1)
    for name in ("tertiary-observe", "tertiary-adapter", "inquisitive-read"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("sweeper.json"))
    daemon_command = sub.add_parser("daemon")
    daemon_command.add_argument("--config", type=Path, default=Path("sweeper.json"))
    daemon_command.add_argument("--interval", type=float, default=60.0)
    daemon_command.add_argument("--once", action="store_true")
    daemon_command.add_argument("--max-backoff", type=float, default=900.0)
    enforcer_command = sub.add_parser("pivot-enforcer")
    enforcer_command.add_argument("--config", type=Path, default=Path("sweeper.json"))
    enforcer_command.add_argument("--watch", action="store_true")
    enforcer_command.add_argument("--poll-seconds", type=float, default=10.0)
    discover_command = sub.add_parser("discover")
    discover_command.add_argument("--config", type=Path, default=Path("sweeper.json"))
    discover_command.add_argument("--category", action="append", default=[])
    discover_command.add_argument("--output", type=Path)
    project_save = sub.add_parser("project-save")
    project_save.add_argument("--config", type=Path, default=Path("sweeper.json"))
    project_save.add_argument("--name", required=True)
    project_save.add_argument("--directory", type=Path, default=Path("sweeper-projects"))
    project_load = sub.add_parser("project-load")
    project_load.add_argument("--name", required=True)
    project_load.add_argument("--config", type=Path, default=Path("sweeper.json"))
    project_load.add_argument("--directory", type=Path, default=Path("sweeper-projects"))
    project_list = sub.add_parser("project-list")
    project_list.add_argument("--directory", type=Path, default=Path("sweeper-projects"))
    sub.add_parser("translator-status")
    translate_command = sub.add_parser("translate")
    translate_command.add_argument("--input", type=Path, required=True)
    translate_command.add_argument("--output", type=Path, required=True)
    translate_command.add_argument("--source-language", required=True)
    translate_command.add_argument("--target-language", required=True)
    translate_command.add_argument("--engine-command")
    for name in ("translation-status", "translation-queue", "translation-run"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("sweeper.json"))
        if name != "translation-status":
            command.add_argument("--target-language", required=True)
        if name == "translation-queue":
            command.add_argument("--source-language", default="en")
    dock_status = sub.add_parser("dock-status")
    dock_status.add_argument("--config", type=Path, default=Path("sweeper.json"))
    dock_validate = sub.add_parser("dock-validate")
    dock_validate.add_argument("--config", type=Path, default=Path("sweeper.json"))
    dock_validate.add_argument("--attestation", type=Path, required=True)
    dock_promote = sub.add_parser("dock-promote")
    dock_promote.add_argument("--config", type=Path, default=Path("sweeper.json"))
    dock_promote.add_argument("--publisher-command", nargs="+", required=True)
    dock_promote.add_argument("--verifier-command", nargs="+", required=True)
    dock_promote.add_argument("--cleanup-command", nargs="+")
    dock_cleanup = sub.add_parser("dock-cleanup")
    dock_cleanup.add_argument("--config", type=Path, default=Path("sweeper.json"))
    dock_cleanup.add_argument("--cleanup-command", nargs="+", required=True)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.config.resolve())
        print(json.dumps({"created": str(args.config.resolve()), "next": "edit sources and policy, then run sweeper validate"}, indent=2))
        return 0
    if args.command == "project-save":
        target = save_project(args.config.resolve(), args.directory, args.name)
        print(json.dumps({"saved": str(target), "project": args.name}, indent=2)); return 0
    if args.command == "project-load":
        target = load_project(args.directory, args.name, args.config.resolve())
        print(json.dumps({"loaded": args.name, "config": str(target)}, indent=2)); return 0
    if args.command == "project-list":
        directory = args.directory.resolve()
        projects = []
        for path in sorted(directory.glob("*.json")) if directory.exists() else []:
            raw = json.loads(path.read_text(encoding="utf-8"))
            projects.append({"name": raw.get("project", {}).get("name", path.stem),
                             "file": str(path),
                             "overallTargetItems": raw.get("project", {}).get("overall_target_items", 0),
                             "dailyTargetItems": raw.get("project", {}).get("daily_target_items", 0)})
        print(json.dumps({"projects": projects, "count": len(projects)}, indent=2)); return 0
    if args.command == "engine-mode":
        path = args.config.resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        current = str(raw.get("engine", {}).get("mode", "ultra"))
        if args.selected:
            raw.setdefault("engine", {})["mode"] = args.selected
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
            load_config(path)
            current = args.selected
        print(json.dumps({"mode": current, "priority": ["ultra", "dual", "v2"],
            "ultraIsPrimary": current in {"ultra", "dual"},
            "dualContract": "Ultra leads; V2 is read-only shadow"}, indent=2))
        return 0
    if args.command == "daemon":
        if args.interval < 5:
            raise SystemExit("daemon interval must be at least five seconds")
        if args.max_backoff < args.interval:
            raise SystemExit("daemon maximum backoff must be at least the base interval")
        return daemon(args.config.resolve(), args.interval, args.once, args.max_backoff)
    if args.command == "activity-log":
        config=load_config(args.config.resolve())
        print(json.dumps(activity_report(config.workspace,args.limit),indent=2)); return 0
    if args.command == "tertiary-mode":
        path = args.config.resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        tertiary = raw.setdefault("tertiary", {})
        if args.selected:
            tertiary["enabled"] = args.selected == "on"
            if args.selected == "off":
                tertiary["adapter_enabled"] = False
        if args.inquisitive:
            tertiary["inquisitive_enabled"] = args.inquisitive == "on"
        if args.adapter:
            tertiary["adapter_enabled"] = args.adapter == "on"
        if args.selected or args.inquisitive or args.adapter:
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        config = load_config(path)
        print(json.dumps({"enabled": config.tertiary.enabled,
            "inquisitiveEnabled": config.tertiary.inquisitive_enabled,
            "adapterEnabled": config.tertiary.adapter_enabled,
            "legacyExecutionPathWhenOff": True,
            "tertiaryAuthority": "none"}, indent=2)); return 0
    if args.command == "bridge-switch":
        path = args.config.resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        bridge = raw.setdefault("bridge", {"enabled": False, "nurture_threshold_percent": 50.0})
        if args.selected:
            bridge["enabled"] = args.selected == "on"
        if args.threshold is not None:
            if not 0 <= args.threshold <= 100:
                raise SystemExit("bridge threshold must be between 0 and 100")
            bridge["nurture_threshold_percent"] = args.threshold
        if args.selected or args.threshold is not None:
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        print(json.dumps(bridge_decision(
            args.accepted, args.target, bool(bridge.get("enabled")),
            float(bridge.get("nurture_threshold_percent", 50.0))), indent=2)); return 0
    if args.command == "tertiary-observe":
        print(json.dumps(tertiary_observe(load_config(args.config.resolve())), indent=2)); return 0
    if args.command == "inquisitive-read":
        print(json.dumps(inquisitive_read(load_config(args.config.resolve())), indent=2)); return 0
    if args.command == "tertiary-adapter":
        print(json.dumps(adapter_view(load_config(args.config.resolve())), indent=2)); return 0
    if args.command == "pivot-enforcer":
        if args.poll_seconds < 5:
            raise SystemExit("pivot-enforcer poll interval must be at least five seconds")
        config = load_config(args.config.resolve())
        while True:
            result = enforce(config)
            print(json.dumps(result, indent=2), flush=True)
            if not args.watch:
                return 2 if result["enforcementRequired"] else 0
            time.sleep(args.poll_seconds)
    if args.command == "translator-status":
        print(json.dumps(capabilities(), indent=2)); return 0
    if args.command == "translate":
        print(json.dumps(translate_file(args.input.resolve(), args.output.resolve(),
            args.source_language, args.target_language, args.engine_command), indent=2)); return 0
    if args.command in {"translation-status", "translation-queue", "translation-run"}:
        config = load_config(args.config.resolve())
        fleet = TranslationFleet(config)
        try:
            if args.command == "translation-status": result = fleet.status()
            elif args.command == "translation-queue":
                result = fleet.queue(args.target_language, args.source_language)
            else: result = fleet.run_batch(args.target_language)
        finally: fleet.close()
        print(json.dumps(result, indent=2)); return 0
    if args.command == "discover":
        config = load_config(args.config.resolve())
        output = args.output.resolve() if args.output else config.workspace / "discovered-sources.json"
        print(json.dumps(discover(args.category or list(DEFAULT_CATEGORIES), output,
                                  config.user_agent), indent=2)); return 0
    if args.command in {"dock-status", "dock-validate", "dock-promote", "dock-cleanup"}:
        config = load_config(args.config.resolve())
        if args.command == "dock-status":
            state = State(config.workspace / "state.sqlite3")
            try: items = staged(state)
            finally: state.close()
            print(json.dumps({"staged": len(items), "liveEnabled": False,
                              "validationPresent": (config.workspace / "dock-validation.json").exists(),
                              "promotionPresent": (config.workspace / "dock-promotion.json").exists()}, indent=2))
            return 0
        if args.command == "dock-validate":
            print(json.dumps(validate_attestation(config.workspace, args.attestation.resolve()), indent=2)); return 0
        if args.command == "dock-cleanup":
            print(json.dumps(cleanup_verified_staging(config.workspace,
                                                       args.cleanup_command), indent=2)); return 0
        result = promote(config.workspace, args.publisher_command, args.verifier_command)
        if args.cleanup_command:
            result["cleanup"] = cleanup_verified_staging(config.workspace, args.cleanup_command)
        print(json.dumps(result, indent=2)); return 0
    config = load_config(args.config.resolve())
    if args.command == "run" and args.engine:
        config.engine_mode = args.engine
    if args.command == "validate":
        print(json.dumps({"valid": True, "sources": len(config.sources), "workspace": str(config.workspace)}, indent=2))
        return 0
    if args.command == "run":
        print(json.dumps(run_selected(config), indent=2))
        return 0
    state = State(config.workspace / "state.sqlite3")
    try:
        if args.command in {"plan", "sources"}:
            plan = build_plan(config, state)
            print(json.dumps(plan if args.command == "plan" else plan["sourceIntelligence"], indent=2))
        else:
            print(json.dumps({"counts": state.counts(), "workspace": str(config.workspace)}, indent=2))
    finally:
        state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
