from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from .model import Config, Policy, Source, Tertiary, Translation


MAX_PROJECT_TARGET = 100_000_000_000
MAX_BATCH_SIZE = 1_000


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    layout = raw.get("layout", {})
    sources = []
    for item in raw.get("sources", []):
        value = dict(item)
        manifest = str(value.get("manifest", ""))
        if urlparse(manifest).scheme not in {"http", "https", "file"}:
            value["manifest"] = str((path.parent / manifest).resolve())
        continuations = []
        for location in value.get("continuation_manifests", []):
            location = str(location)
            if urlparse(location).scheme not in {"http", "https", "file"}:
                location = str((path.parent / location).resolve())
            continuations.append(location)
        value["continuation_manifests"] = continuations
        sources.append(Source(**value))
    config = Config(
        workspace=(path.parent / raw.get("workspace", "./sweeper-data")).resolve(),
        user_agent=str(raw.get("user_agent", "Institutional-Sweeper/0.1 (+contact-required)")),
        project_name=str(raw.get("project", {}).get("name", path.stem)),
        overall_target_items=int(raw.get("project", {}).get("overall_target_items", 0)),
        daily_target_items=int(raw.get("project", {}).get("daily_target_items", 0)),
        major_slots=int(layout.get("major_slots", 2)),
        minor_slots=int(layout.get("minor_slots", 2)),
        sources=sources,
        policy=Policy(**raw.get("policy", {})),
        translation=Translation(**raw.get("translation", {})),
        tertiary=Tertiary(**raw.get("tertiary", {})),
        engine_mode=str(raw.get("engine", {}).get("mode", "ultra")),
        nurture_threshold=int(raw.get("nurture", {}).get("threshold", 30)),
    )
    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    allowed_tertiary_signals = {"nurture", "pivot", "continuation"}
    if len(config.tertiary.signals) != len(set(config.tertiary.signals)):
        raise ValueError("tertiary signals must be unique")
    if any(signal not in allowed_tertiary_signals for signal in config.tertiary.signals):
        raise ValueError("tertiary signals may contain only nurture, pivot, and continuation")
    if config.tertiary.adapter_enabled and not config.tertiary.enabled:
        raise ValueError("the tertiary adapter cannot be enabled while tertiary mode is off")
    if config.engine_mode not in {"ultra", "dual", "v2"}:
        raise ValueError("engine mode must be ultra, dual, or v2")
    if not config.project_name.strip():
        raise ValueError("project name cannot be empty")
    if not 1 <= config.nurture_threshold <= 10_000:
        raise ValueError("nurture threshold must be between 1 and 10,000")
    for label, value in (("overall target", config.overall_target_items),
                         ("daily target", config.daily_target_items)):
        if value < 0 or value > MAX_PROJECT_TARGET:
            raise ValueError(f"{label} must be between 0 and {MAX_PROJECT_TARGET:,}")
    if config.major_slots != 2 or not 1 <= config.minor_slots <= 6:
        raise ValueError("Sweeper requires two major slots and between one and six light slots")
    if not 1 <= config.translation.batch_size <= MAX_BATCH_SIZE:
        raise ValueError("translation batch size must be between 1 and 1,000")
    if not config.translation.staging_collection.strip():
        raise ValueError("translation staging collection cannot be empty")
    if config.translation.enabled and config.translation.staging_collection.startswith("REPLACE_WITH_"):
        raise ValueError("replace the translation staging collection placeholder before enabling translation")
    from .translation import LANGUAGES
    if any(language not in LANGUAGES for language in config.translation.target_languages):
        raise ValueError("translation target language is unsupported")
    if not config.user_agent or "contact-required" in config.user_agent:
        raise ValueError("set a truthful user_agent containing institutional contact information")
    if len(config.policy.required_metadata_fields) != len(set(config.policy.required_metadata_fields)):
        raise ValueError("required metadata fields must be unique")
    if any(not str(value).strip() for value in config.policy.required_metadata_fields):
        raise ValueError("required metadata fields cannot be empty")
    if any(not str(value).strip() for value in config.policy.allowed_file_extensions):
        raise ValueError("allowed file extensions cannot be empty")
    ids = [source.id for source in config.sources]
    if len(ids) != len(set(ids)):
        raise ValueError("source IDs must be unique")
    occupied = [(source.lane, source.slot) for source in config.sources if source.enabled]
    if len(occupied) != len(set(occupied)):
        raise ValueError("each enabled major/minor slot may contain only one source")
    for source in config.sources:
        maximum = config.major_slots if source.lane == "major" else config.minor_slots
        if source.lane not in {"major", "minor"} or not 1 <= source.slot <= maximum:
            raise ValueError(f"invalid lane/slot for {source.id}")
        if source.workers < 1 or source.workers > (4 if source.lane == "major" else 2):
            raise ValueError(f"unsafe worker count for {source.id}")
        if not 1 <= source.batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"batch size must be between 1 and 1,000 for {source.id}")
        if source.requests_per_second <= 0 or source.requests_per_second > 10:
            raise ValueError(f"invalid request rate for {source.id}")
        if source.target_items < 0:
            raise ValueError(f"target items cannot be negative for {source.id}")
        if source.estimated_eligible_items < 0 or source.estimated_eligible_items > MAX_PROJECT_TARGET:
            raise ValueError(f"estimated eligible items out of range for {source.id}")
        if source.estimated_daily_items < 0 or source.estimated_daily_items > MAX_PROJECT_TARGET:
            raise ValueError(f"estimated daily items out of range for {source.id}")
        if source.assistance_mode not in {"sweeper-choice", "disabled"}:
            raise ValueError(f"invalid assistance mode for {source.id}")
        if len(source.continuation_manifests) > 1000:
            raise ValueError(f"continuation manifest pool is too large for {source.id}")
