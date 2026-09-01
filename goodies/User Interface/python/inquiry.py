#!/usr/bin/env python3
"""Dependency-free, read-only staged/live inquiry server."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
UI = ROOT / "static"
SCOPES = {"staged", "live"}
STAGES = ("discovered", "qualified", "retrieved", "converted", "validated", "published", "live-verified")


def _expand(value: str) -> str:
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), value)


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("records", "items", "documents"):
            if isinstance(value.get(key), list):
                return _rows(value[key])
    return []


def _read_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict): result.append(value)
        return result
    return _rows(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Connection:
    name: str
    scope: str
    kind: str
    location: str
    headers: dict[str, str] = field(default_factory=dict)

    def load(self) -> list[dict[str, Any]]:
        if self.kind == "http":
            request = Request(_expand(self.location), headers={key: _expand(value) for key, value in self.headers.items()})
            with urlopen(request, timeout=20) as response:
                return _rows(json.load(response))
        path = Path(_expand(self.location)).expanduser().resolve()
        if path.is_dir():
            records: list[dict[str, Any]] = []
            for child in sorted((*path.glob("*.json"), *path.glob("*.jsonl"))): records.extend(_read_file(child))
            return records
        return _read_file(path)


class Catalog:
    def __init__(self, connections: Iterable[Connection]): self.connections = list(connections)

    def records(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        records, errors = [], []
        for connection in self.connections:
            try:
                for index, source in enumerate(connection.load()):
                    title = str(source.get("title") or "").strip()
                    ident = str(source.get("id") or source.get("record_id") or "").strip()
                    if not title or not ident: continue
                    row = dict(source); row["id"] = ident; row["title"] = title
                    row["scope"] = connection.scope if connection.scope in SCOPES else str(row.get("scope", "staged"))
                    if row["scope"] not in SCOPES: row["scope"] = "staged"
                    row["connection"] = connection.name
                    row.setdefault("stage", "live-verified" if row["scope"] == "live" else "discovered")
                    records.append(row)
            except Exception as exc:
                errors.append({"connection": connection.name, "error": str(exc)})
        records.sort(key=lambda row: (row["scope"], str(row.get("title", "")).casefold(), row["id"]))
        return records, errors


def _search(rows: list[dict[str, Any]], params: dict[str, list[str]]) -> list[dict[str, Any]]:
    one = lambda key: (params.get(key) or [""])[0].strip()
    query, scope, author, category, stage = map(one, ("q", "scope", "author", "category", "stage"))
    date_from, date_to = one("from"), one("to")
    custom_key, custom_value = one("field"), one("value")
    tokens = query.casefold().split()
    def match(row: dict[str, Any]) -> bool:
        haystack = json.dumps(row, ensure_ascii=False).casefold()
        date = str(row.get("date") or row.get("year") or "")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return (not tokens or all(token in haystack for token in tokens)) and \
            (not scope or scope == "all" or row.get("scope") == scope) and \
            (not author or author.casefold() in str(row.get("author", "")).casefold()) and \
            (not category or row.get("category") == category) and (not stage or row.get("stage") == stage) and \
            (not date_from or date >= date_from) and (not date_to or date <= date_to) and \
            (not custom_key or custom_value.casefold() in str(metadata.get(custom_key, row.get(custom_key, ""))).casefold())
    return [row for row in rows if match(row)]


class Handler(BaseHTTPRequestHandler):
    catalog: Catalog

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            records, errors = self.catalog.records()
            if parsed.path == "/api/health": return self._json({"ok": not errors, "records": len(records), "errors": errors})
            if parsed.path == "/api/facets":
                keys = sorted({key for row in records for key in (row.get("metadata") or {}) if isinstance(row.get("metadata"), dict)})
                return self._json({"categories": sorted({str(r.get("category")) for r in records if r.get("category")}), "stages": list(STAGES), "customFields": keys, "errors": errors})
            if parsed.path == "/api/records":
                matches = _search(records, parse_qs(parsed.query)); limit = min(1000, max(1, int((parse_qs(parsed.query).get("limit") or [250])[0])))
                return self._json({"records": matches[:limit], "matched": len(matches), "total": len(records), "errors": errors})
            return self._json({"error": "not found"}, 404)
        target = UI / ("index.html" if parsed.path == "/" else parsed.path.lstrip("/"))
        try: target = target.resolve(); target.relative_to(UI.resolve())
        except ValueError: return self.send_error(403)
        if not target.is_file(): return self.send_error(404)
        body = target.read_bytes(); self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, template: str, *args: Any) -> None: print(template % args)


def connections(args: argparse.Namespace) -> list[Connection]:
    values: list[dict[str, Any]] = []
    if args.config: values.extend(json.loads(args.config.read_text(encoding="utf-8")).get("connections", []))
    if args.staged: values.append({"name": "Staging", "scope": "staged", "kind": "http" if str(args.staged).startswith("http") else "path", "location": str(args.staged)})
    if args.live: values.append({"name": "Live", "scope": "live", "kind": "http" if str(args.live).startswith("http") else "path", "location": str(args.live)})
    return [Connection(name=str(v.get("name", "Connection")), scope=str(v.get("scope", "staged")), kind=str(v.get("kind", "path")), location=str(v["location"]), headers={str(k): str(x) for k, x in v.get("headers", {}).items()}) for v in values]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path); parser.add_argument("--staged"); parser.add_argument("--live")
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(); Handler.catalog = Catalog(connections(args))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Inquiry listening on http://{args.host}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
