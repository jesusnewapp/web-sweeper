from __future__ import annotations

import json
import http.client
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_CATEGORIES = (
    "open public archives", "public domain books complete text",
    "open government document repository", "open scientific datasets",
    "open legal document archive", "open educational resources",
    "public domain music audio downloads", "open licensed video archives",
    "public domain image collections", "open access comics downloads",
    "open geospatial data archives", "open research software archives",
)

TRANSIENT_NETWORK_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    socket.timeout,
    http.client.IncompleteRead,
    ConnectionResetError,
    BrokenPipeError,
)


def _read_rss(request: urllib.request.Request, *, retries: int = 5,
              timeout_seconds: int = 60) -> bytes:
    """Read one discovery feed with bounded transient-failure retries."""
    attempts = max(1, retries)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read(2_000_000)
        except TRANSIENT_NETWORK_ERRORS as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(min(16.0, 2.0 ** attempt))
    assert last_error is not None
    raise last_error


def discover(categories: list[str], output: Path, user_agent: str,
             results_per_category: int = 20) -> dict:
    """Discover candidate source sites without acquiring their content."""
    rows: list[dict] = []
    seen: set[str] = set()
    errors: list[dict] = []
    for category in categories:
        url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(category)
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        try:
            root = ET.fromstring(_read_rss(request))
            for item in root.findall(".//item")[:results_per_category]:
                link = (item.findtext("link") or "").strip()
                title = (item.findtext("title") or "").strip()
                parsed = urllib.parse.urlsplit(link)
                domain = parsed.netloc.casefold().removeprefix("www.")
                if not domain or domain in seen:
                    continue
                seen.add(domain)
                rows.append({"domain": domain, "url": link, "title": title,
                    "matched_category": category,
                    "status": "operator-review-and-source-contract-required"})
        except Exception as error:
            errors.append({"category": category, "error": f"{type(error).__name__}: {error}"})
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "categories": categories,
        "candidate_sites": rows,
        "errors": errors,
        "notice": "Discovery is not permission. Review terms, robots, rights, privacy, APIs, and data boundaries before configuration.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return payload
