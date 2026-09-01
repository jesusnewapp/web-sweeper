# Inquiry — staged/live collection interface

Inquiry is an optional, source-neutral interface for exploring an operator's own
staged and live collections. It includes two clients with the same record and
connector contract:

- `python/`: a dependency-free Python server and responsive browser UI.
- `flutter/`: a Flutter mobile application for Android, iOS, macOS, Windows,
  Linux, and web.

Neither client ships Codex data, credentials, Firebase project IDs, or a live
publication action. Inquiry is read-only by design: **staged** and **live** are
visually and semantically distinct, and a staged record is never described as
published.

## Plug Web Sweeper into your interface

After Web Sweeper collects and stages an authorized collection, point Inquiry at
the resulting operator-owned JSON/JSONL export or at an HTTPS gateway that emits
the record contract below. The Python edition can read a hard drive or server
folder directly and expose it to phones on the local network. The Flutter edition
can connect to that Python API, a self-hosted API, Firebase Cloud Function, or any
cloud endpoint returning the same JSON shape. Web Sweeper continues to own
collection, hashing, provenance, policy, and staging; Inquiry supplies the smooth
search, inspection, and playback surface.

## Record contract

Only `id` and `title` are required. All other fields are optional and unknown
fields are retained in `metadata` for operator-defined filters.

```json
{
  "id": "stable-source-id",
  "scope": "staged",
  "title": "Example work",
  "author": "Example author",
  "date": "1894",
  "category": "History",
  "description": "Short operator-supplied description.",
  "subjects": ["missions", "biography"],
  "source": "Example Archive",
  "stage": "validated",
  "rights": "Public domain in the United States",
  "language": "English",
  "url": "https://operator.example/reader/stable-source-id",
  "media": {
    "kind": "audio",
    "url": "https://operator.example/media/stable-source-id.mp3",
    "mimeType": "audio/mpeg",
    "posterUrl": "https://operator.example/media/poster.jpg",
    "downloadUrl": "https://operator.example/media/stable-source-id.mp3"
  },
  "metadata": {"collection": "Shelf A", "edition": "First"}
}
```

Sweeper-compatible stage values are `discovered`, `qualified`, `retrieved`,
`converted`, `validated`, `published`, and `live-verified`. Custom values remain
displayable, but do not gain a stronger meaning automatically.

## Player and reader adapters

Every record may include a `media` object. Inquiry selects an adapter from
`media.kind` or `media.mimeType`:

- `audio` and `video`: in-page HTML5 playback in Python; system-player handoff
  in Flutter, suitable for operator-controlled streaming URLs.
- `image`: responsive in-page viewer.
- `text`, `json`, and `web`: contained reader panel or operator reader link.
- `rom` and `software`: metadata, checksum, rights, and an explicit external
  player/open action. Inquiry never bundles an emulator or executes a ROM in the
  browser. Configure a trusted emulator/player URI in
  `media.options.playerUrlTemplate` (for example, an installed player's documented
  deep-link pattern containing `{url}`) or use the guidance in
  [`../ROM_PLAYERS.md`](../ROM_PLAYERS.md).
- any future media: a safe open/download fallback, so operators can add custom
  formats without changing the collection contract.

Adapters are display and handoff mechanisms; they do not establish permission.
Only expose media the operator is authorized to serve. Keep `url` for the record
or web-reader page and `media.url` for the playable object. Custom adapter data
can live under `media.options` and project-specific searchable fields under
`metadata`.

Software remains staging/inspection-only. Describe packages with fields such as
`media.options.platforms` (`["Windows", "macOS"]`), `architectures`, `version`,
`sha256`, and `requirements`. Inquiry displays those compatibility facts and may
open an authorized download or project page, but does not install or execute the
software.

## Python quick start

```bash
python3 "goodies/User Interface/python/inquiry.py" \
  --staged /path/to/staged.jsonl \
  --live /path/to/live.jsonl \
  --port 8787
```

Then open `http://127.0.0.1:8787`. Each input may be a JSON/JSONL file, a folder
containing JSON/JSONL files, or an `http(s)` endpoint. Use `--config` for saved
connections; see `config.example.json`. Environment variables such as API keys
can be expanded with `${VARIABLE_NAME}`. Secrets should remain outside Git.

The Python server also exposes `GET /api/records`, `GET /api/facets`, and
`GET /api/health`, so the Flutter client can connect to it on a MacBook, NAS,
private server, or cloud host.

## Flutter quick start

```bash
cd "goodies/User Interface/flutter"
flutter pub get
flutter run --dart-define=INQUIRY_ENDPOINT=http://127.0.0.1:8787/api/records
```

For a physical phone, replace `127.0.0.1` with the server's LAN HTTPS address.
Mobile release builds should use a trusted HTTPS certificate; if an operator
chooses plain HTTP for isolated local development, configure the platform's
debug-only network policy explicitly rather than weakening release security.
The endpoint may return a JSON array or `{ "records": [...] }`. Firebase users
can place a small authenticated HTTPS/Cloud Function in front of Firestore and
return that same shape; this avoids embedding privileged credentials in a public
mobile application.

## Safety and deployment

- Use HTTPS outside a trusted local network.
- Put authentication and authorization at the server or cloud gateway.
- Never embed service-account keys in either client.
- Expose only fields intended for the viewer.
- Keep the production writer and its lease outside Inquiry. Inquiry does not
  validate, promote, publish, delete, or clean staging.
