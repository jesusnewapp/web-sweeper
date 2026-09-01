from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from http.client import IncompleteRead
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, FrozenSet, Optional, Tuple
import urllib.request
import urllib.error
from urllib.parse import urlencode


SEARCH_ENDPOINT = "https://archive.org/services/search/v1/scrape"


def _validate_partition(modulus: int, buckets: FrozenSet[int]) -> None:
    if modulus < 2:
        raise ValueError("partition modulus must be at least 2")
    if not buckets:
        raise ValueError("partition buckets cannot be empty")
    if any(bucket < 0 or bucket >= modulus for bucket in buckets):
        raise ValueError("partition bucket must be within the configured modulus")


def partition_bucket(identifier: str, modulus: int = 7) -> int:
    if not identifier.strip():
        raise ValueError("Archive identifier cannot be empty")
    if modulus < 2:
        raise ValueError("partition modulus must be at least 2")
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return int(digest, 16) % modulus


def owns_identifier(identifier: str, modulus: int,
                    buckets: FrozenSet[int]) -> bool:
    _validate_partition(modulus, buckets)
    return partition_bucket(identifier, modulus) in buckets


@dataclass(frozen=True)
class ArchiveQuery:
    query: str
    fields: Tuple[str, ...]
    sort: Tuple[str, ...]
    rows: int

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("Archive query cannot be empty")
        if not 1 <= self.rows <= 500:
            raise ValueError("Archive query rows must be between 1 and 500")
        if "identifier" not in self.fields:
            raise ValueError("Archive query fields must include identifier")
        if not any(value.casefold() == "identifier asc" for value in self.sort):
            raise ValueError("Archive query sort must include identifier asc")


@dataclass(frozen=True)
class ArchiveIdentity:
    identifier: str
    title: str
    creators: Tuple[str, ...]
    alternate_titles: Tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class ArchiveSearchPage:
    records: Tuple[ArchiveIdentity, ...]
    next_cursor: Optional[str]
    num_found: int


@dataclass(frozen=True)
class ArchiveDiscoveryConfig:
    query: ArchiveQuery
    modulus: int
    buckets: FrozenSet[int]
    max_candidates: int
    requests_per_second: float
    checkpoint_root: Path
    user_agent: str = "Web-Sweeper/0.7 (+https://github.com/jesusnewapp/web-sweeper)"

    def __post_init__(self) -> None:
        _validate_partition(self.modulus, self.buckets)
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if not 0 < self.requests_per_second <= 10:
            raise ValueError("requests_per_second must be greater than zero and at most 10")


@dataclass(frozen=True)
class DiscoveryReport:
    screened_source: int
    screened_owned: int
    checkpoints: int
    completed_reason: str
    next_cursor: Optional[str]


def build_search_url(query: ArchiveQuery, cursor: Optional[str] = None) -> str:
    parameters: list[tuple[str, str]] = [
        ("q", query.query),
        ("fields", ",".join(query.fields)),
        ("count", str(query.rows)),
    ]
    if cursor is not None:
        parameters.append(("cursor", cursor))
    return f"{SEARCH_ENDPOINT}?{urlencode(parameters)}"


def _strings(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, list) else [value]
    return tuple(str(item).strip() for item in values if str(item).strip())


def parse_search_page(payload: bytes) -> ArchiveSearchPage:
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Archive response is not valid JSON") from error
    if not isinstance(raw, dict):
        raise ValueError("Archive response is missing response.docs")
    response = raw.get("response")
    if isinstance(raw.get("items"), list):
        response = {"docs": raw["items"], "numFound": raw.get("total", len(raw["items"]))}
    if not isinstance(response, dict) or not isinstance(response.get("docs"), list):
        raise ValueError("Archive response is missing response.docs")
    records = []
    for document in response["docs"]:
        if not isinstance(document, dict) or not str(document.get("identifier", "")).strip():
            raise ValueError("Archive record is missing identifier")
        alternate = document.get("alternative_title", document.get("alternate_title"))
        records.append(ArchiveIdentity(
            identifier=str(document["identifier"]).strip(),
            title=str(document.get("title", "")).strip(),
            creators=_strings(document.get("creator")),
            alternate_titles=_strings(alternate),
            raw=dict(document),
        ))
    try:
        num_found = int(response.get("numFound", len(records)))
    except (TypeError, ValueError) as error:
        raise ValueError("Archive response has invalid numFound") from error
    next_cursor = raw.get("cursor", raw.get("nextCursorMark"))
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise ValueError("Archive response has invalid nextCursorMark")
    return ArchiveSearchPage(tuple(records), next_cursor, num_found)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        output.write(_canonical_json(value) + "\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def _verify_checkpoints(root: Path, count: int) -> None:
    for number in range(1, count + 1):
        checkpoint = root / f"checkpoint-{number:06d}.jsonl"
        receipt_path = root / f"checkpoint-{number:06d}.receipt.json"
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"checkpoint receipt {number} is unreadable") from error
        if receipt.get("sha256") != digest or receipt.get("checkpoint") != checkpoint.name:
            raise ValueError(f"checkpoint receipt {number} does not match")


def _fetch_page(request: urllib.request.Request, config: ArchiveDiscoveryConfig,
                opener, sleeper) -> Optional[ArchiveSearchPage]:
    for attempt in range(4):
        try:
            with opener(request, timeout=90) as response:
                return parse_search_page(response.read())
        except urllib.error.HTTPError as error:
            if error.code != 429 and not 500 <= error.code <= 599:
                raise
        except (socket.timeout, TimeoutError, ConnectionError, IncompleteRead,
                urllib.error.URLError):
            pass
        if attempt < 3:
            sleeper((1.0 / config.requests_per_second) * (2 ** attempt))
    return None


def discover_archive(config: ArchiveDiscoveryConfig, *, opener=urllib.request.urlopen,
                     sleeper=time.sleep,
                     progress: Optional[Callable[[DiscoveryReport], None]] = None
                     ) -> DiscoveryReport:
    root = config.checkpoint_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    frontier_path = root / "frontier.json"
    if frontier_path.exists():
        try:
            frontier = json.loads(frontier_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("discovery frontier is unreadable") from error
    else:
        frontier = {"screenedSource": 0, "screenedOwned": 0,
                    "checkpoints": 0, "nextCursor": None,
                    "completedReason": ""}
    _verify_checkpoints(root, int(frontier["checkpoints"]))
    if frontier.get("completedReason"):
        return DiscoveryReport(int(frontier["screenedSource"]),
                               int(frontier["screenedOwned"]),
                               int(frontier["checkpoints"]),
                               str(frontier["completedReason"]),
                               frontier.get("nextCursor"))

    cursor = frontier.get("nextCursor")
    while int(frontier["screenedOwned"]) < config.max_candidates:
        url = build_search_url(config.query, cursor)
        request = urllib.request.Request(url, headers={"User-Agent": config.user_agent})
        page = _fetch_page(request, config, opener, sleeper)
        if page is None:
            return DiscoveryReport(int(frontier["screenedSource"]),
                                   int(frontier["screenedOwned"]),
                                   int(frontier["checkpoints"]),
                                   "transient-deferred", cursor)
        owned = []
        for record in page.records:
            frontier["screenedSource"] += 1
            if not owns_identifier(record.identifier, config.modulus, config.buckets):
                continue
            owned.append(record)
            frontier["screenedOwned"] += 1
            if frontier["screenedOwned"] >= config.max_candidates:
                break
        checkpoint_number = int(frontier["checkpoints"]) + 1
        checkpoint = root / f"checkpoint-{checkpoint_number:06d}.jsonl"
        body = "".join(_canonical_json({
            "identifier": record.identifier,
            "title": record.title,
            "creators": list(record.creators),
            "alternateTitles": list(record.alternate_titles),
            "raw": record.raw,
        }) + "\n" for record in owned).encode("utf-8")
        temporary = checkpoint.with_suffix(".jsonl.tmp")
        with temporary.open("wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(checkpoint)
        _atomic_json(root / f"checkpoint-{checkpoint_number:06d}.receipt.json", {
            "checkpoint": checkpoint.name,
            "sha256": hashlib.sha256(body).hexdigest(),
            "ownedRecords": len(owned),
        })
        frontier["checkpoints"] = checkpoint_number
        derived_next = page.next_cursor
        frontier["nextCursor"] = derived_next
        if frontier["screenedOwned"] >= config.max_candidates:
            frontier["completedReason"] = "candidate-ceiling"
        elif derived_next is None or derived_next == cursor:
            frontier["completedReason"] = "source-exhausted"
        _atomic_json(frontier_path, frontier)
        if progress:
            progress(DiscoveryReport(int(frontier["screenedSource"]),
                                     int(frontier["screenedOwned"]),
                                     int(frontier["checkpoints"]),
                                     str(frontier["completedReason"]),
                                     frontier.get("nextCursor")))
        if frontier["completedReason"]:
            break
        cursor = derived_next
        sleeper(1.0 / config.requests_per_second)
    return DiscoveryReport(int(frontier["screenedSource"]),
                           int(frontier["screenedOwned"]),
                           int(frontier["checkpoints"]),
                           str(frontier["completedReason"]),
                           frontier.get("nextCursor"))
