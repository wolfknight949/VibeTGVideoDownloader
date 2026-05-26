# VibeTG — Telegram Video Downloader

A NiceGUI web app for browsing, filtering, and downloading videos from Telegram groups, channels, and forum chats.

---

## Quick Start

### 1. Clone and set up the environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Create `.env`

Get values from [my.telegram.org/apps](https://my.telegram.org/apps):

```env
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890abcdef1234567890
```

### 3. Authorize your Telegram account (once)

```bash
python login.py
```

This creates `tg_parser_session.session`. Keep it secret — it grants full account access.

### 4. Run the app

```bash
python app.py
```

Opens at [http://localhost:8080](http://localhost:8080).

> **Hot-reload** is enabled. Saving any Python file automatically restarts the server. In-memory state (scan results, download queue) resets on each reload — expected during development.

---

## How to Use

### Left drawer — Connection Config

- **API ID / API Hash** — loaded from `.env` automatically.
- **Session Profile Name** — defaults to `tg_parser_session`. Change only if managing multiple Telegram accounts.

### Left drawer — Download Settings

- **Downloads Folder** — where completed files, `.part` partials, and `.meta.json` resume metadata are stored. Defaults to `downloads`.
- **Parallel chunks per file** — simultaneous MTProto chunk streams per file. Safe range: 2–4.
- **Parallel files** — how many files download at the same time (1–3). Each parallel file uses its own derived session slot to avoid SQLite locking. Default: 1.

### Scan Group

1. Enter a group username, invite link, or chat reference in **Target Group**.
2. Optionally type a **Search Messages** query (server-side, passed to Telegram directly).
3. Set **Items** to control batch size (default 50).
4. Click **Scan Group** for one batch, or **Scan All** to page until history is exhausted.
5. **Recent groups** — successful scans save the group handle automatically. Click a saved chip to fill the input, or ✕ to remove it.

> Search is server-side. Changing the search text requires a new scan — existing results are not filtered locally.

### Topic & Hashtag Filters

- For forum chats, results are grouped under collapsed topic sections.
- **Resolution filter** (blue buttons) — filter posts by detected video resolution within a topic.
- **Hashtag filter** (purple buttons) — filter posts by hashtags found in message text within a topic.
- Active filters show a bold checkmark prefix and a glowing border. A **🔍 Filters active** badge appears on the topic header.
- **Clear Filters** removes all active filters for that topic.

### Download Monitor

- Auto-refreshes every 0.5 s while downloads are active; idle otherwise.
- Active strip shows filename (or "N files downloading in parallel"), aggregate speed, ETA, and a progress bar.
- Each queued item shows live `XX% · Y.Y MB/s` while downloading.
- **Stop** halts the background worker gracefully; partial files and metadata are preserved for resume.
- **Resume All** re-queues all interrupted downloads found in `downloads/`.

### Parsed Media Inventory

- Videos are grouped into posts by Telegram `grouped_id`.
- Posts are nested under collapsed topic sections.
- Select individual videos or entire posts, then click **Download Selected**.
- **Load more posts** continues pagination with the same search query.
- **Load more topics** pages additional forum topic summaries.

---

## Features

- Telegram server-side search during scan
- Forum chat support — lazy topic loading on demand
- Hashtag filtering per topic
- Resolution filtering per topic
- Recent groups persistence (`recent_groups.json`)
- Resumable downloads — `.part` + `.meta.json` survive interruption
- Parallel chunk download per file (MTProto multi-stream)
- Parallel file downloads (1–3 simultaneous files, each with its own session slot)
- Per-file live progress in queue view
- Hot-reload development mode

---

## Repository Layout

```text
TelegramVideoDownloader/
├── app.py                 # NiceGUI presentation layer
├── telegram_backend.py    # Telegram, download, filesystem, queue, and state logic
├── login.py               # One-time Telegram authorization helper
├── requirements.txt
├── recent_groups.json     # Persisted recent group handles (auto-created)
├── downloads/             # Completed files, *.part partials, *.meta.json metadata
├── .env                   # Local secrets — never commit
└── venv/                  # Local virtualenv — never commit
```

---

## Storage Semantics

All download state lives inside `downloads/`:

- `<filename>` — completed, size-verified file
- `<filename>.part` — in-progress partial download
- `<filename>.meta.json` — resume metadata (msg ID, chat, expected size, topic)
- Topic subfolders mirror the forum structure when applicable

A file is considered complete **only** when the final filename exists without a `.part` or `.meta.json` sibling.

---

## Architecture

### `app.py` — presentation layer

Owns layout, NiceGUI wiring, scan form, download monitor, inventory rendering, and all UI-only helpers. Calls backend functions and writes to the shared `state` dict.

### `telegram_backend.py` — service layer

Owns Telegram client creation, derived session management, scan and pagination via Telethon, queue and progress bookkeeping, resumable partial file detection, parallel chunk and parallel file download logic, and background worker lifecycle.

For implementation guidance aimed at coding agents, see `AGENTS.md`.

---

## Troubleshooting

**Missing credentials / zero API ID** — create or fix `.env`.

**Authentication needed** — run `python login.py`. Derived scan/download sessions are recreated from the base session automatically if missing.

**Search appears stale** — search is server-side. Run a new scan after changing the query.

**File is `.part` + `.meta.json` but no final file** — download was interrupted. Use Resume in the monitor.

**Downloads feel slow** — try increasing `Parallel chunks per file` (2–4). Beyond 4 risks Telegram rate-limits.

**`database is locked` error** — each parallel file download uses its own derived session slot (`__download_0`, `__download_1`, etc.). If you see this, reduce `Parallel files` to 1.

---

## Security Notes

- Never commit `.env`.
- Never commit `*.session` files.
- All session files (base + derived) grant full Telegram account access.
- Filenames are sanitized before use.
- Output paths are restricted to the repository root.

---

## Known Gaps

- No committed `.env.example` yet.
- No automated test suite yet.
- Validation is compile-check plus manual verification.

