# Goodies standalone staged/live indexer

The Goodies folder also includes an [open-source ROM player guide](ROM_PLAYERS.md)
for testing rights-free game acquisitions without bundling third-party binaries.

This optional tool builds a local full-text index for an operator's own staged
and live records. It is not part of Sweeper acquisition and contains no Codex
titles, private categories, backend identifiers, or credentials.

Input is UTF-8 JSONL with operator-owned values such as `id`, `title`,
`category`, `subjects`, `keywords`, `description`, `text`, and `metadata`.
Only `id` and `title` are required. Supply an optional category file so records
outside the operator's taxonomy fail closed.

```bash
python3 goodies/indexer.py build --database my-index.sqlite3 \
  --input staged.jsonl --scope staged --categories my-categories.json
python3 goodies/indexer.py build --database my-index.sqlite3 \
  --input live.jsonl --scope live --categories my-categories.json
python3 goodies/indexer.py search --database my-index.sqlite3 \
  --query 'example phrase' --scope staged
python3 goodies/indexer.py search --database my-index.sqlite3 \
  --query 'example phrase' --scope live --category 'My Category'
python3 goodies/indexer.py export --database my-index.sqlite3 \
  --output goodies/ui/index.json
```

The index is incremental and content-hash aware: unchanged records are skipped,
while changed records replace only their own `(scope,id)` entry. Staged and live
records remain independently searchable even when they share an ID.

The [`ui/`](ui/) folder is a dependency-free interface starter. Export the
index to `ui/index.json`, serve the folder with any static web server, and adapt
the colors, record links, fields, or backend integration to the project.

For a polished, connector-ready interface, use [`User Interface/`](User%20Interface/).
It includes a dependency-free Python server/browser UI and a Flutter mobile app
with the same staged/live record contract, advanced title/author/date/category/
custom-field inquiry, and media adapters for web and JSON readers, text, images,
audio, video, ROM-player handoff, and future operator-defined formats. Point it
at Web Sweeper exports, a local hard drive or server, an HTTPS gateway, or a
Firebase Cloud Function. It is read-only and never promotes staging to live.

For operators and developers, [`developer_interface/`](developer_interface/)
provides a responsive Flutter dashboard for Android, iOS, macOS, Windows,
Linux, and web plus a Python/Tk desktop companion. It includes authenticated
controller connectivity, lane health colors, source/batch/upload controls,
allow-listed Switch/Bridge/Reset/Upload requests, readable-text color choices,
and a small four-pad waiting game. Public control actions are disabled until an
operator explicitly configures trusted host commands.
