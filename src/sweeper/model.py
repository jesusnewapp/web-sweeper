from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Candidate:
    source_id: str
    item_id: str
    url: str
    title: str = ""
    language: str = ""
    license: str = ""
    rights_evidence_url: str = ""
    media_type: str = "application/octet-stream"
    artifact_class: str = "unspecified"
    data_class: str = "unspecified"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Source:
    id: str
    slot: int
    lane: str
    manifest: str
    enabled: bool = True
    requests_per_second: float = 1.0
    workers: int = 1
    batch_size: int = 50
    headers: Dict[str, str] = field(default_factory=dict)
    continuation_manifests: List[str] = field(default_factory=list)
    target_items: int = 0
    estimated_eligible_items: int = 0
    estimated_daily_items: int = 0
    assistance_mode: str = "sweeper-choice"


@dataclass
class Policy:
    languages: List[str] = field(default_factory=list)
    licenses: List[str] = field(default_factory=list)
    media_types: List[str] = field(default_factory=list)
    artifact_classes: List[str] = field(default_factory=list)
    data_classes: List[str] = field(default_factory=list)
    minimum_bytes: int = 1
    maximum_bytes: Optional[int] = None
    require_language: bool = True
    require_license: bool = True
    require_rights_evidence: bool = False
    required_metadata_fields: List[str] = field(default_factory=list)
    allowed_file_extensions: List[str] = field(default_factory=list)
    require_expected_sha256: bool = False
    verify_zip_integrity: bool = False
    reviewer_command: List[str] = field(default_factory=list)


@dataclass
class Translation:
    enabled: bool = False
    batch_size: int = 50
    staging_collection: str = "REPLACE_WITH_YOUR_TRANSLATION_STAGING_COLLECTION"
    target_languages: List[str] = field(default_factory=list)
    notifier_command: List[str] = field(default_factory=list)
    validator_command: List[str] = field(default_factory=list)
    stager_command: List[str] = field(default_factory=list)


@dataclass
class Tertiary:
    """Optional powerless observations and a separately toggled consumer."""
    enabled: bool = False
    inquisitive_enabled: bool = False
    adapter_enabled: bool = False
    signals: List[str] = field(default_factory=lambda: ["nurture", "pivot", "continuation"])


@dataclass
class Config:
    workspace: Path
    user_agent: str
    project_name: str
    overall_target_items: int
    daily_target_items: int
    major_slots: int
    minor_slots: int
    sources: List[Source]
    policy: Policy
    translation: Translation
    tertiary: Tertiary = field(default_factory=Tertiary)
    engine_mode: str = "ultra"
    nurture_threshold: int = 30
