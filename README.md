# VibeTG — Telegram Video Downloader

A NiceGUI web app for browsing, filtering, and downloading videos from Telegram groups, channels, and forum chats.

---

## Quick Start

### 1. Clone and set up the environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### 2. Create `.env`

Copy the provided template and fill in your values from [my.telegram.org/apps](https://my.telegram.org/apps):

```bash
cp .env.example .env
```

```env
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890abcdef1234567890
```

### 3. Authorize your Telegram account (once)

```bash
python login.py
```

This creates `sessions/tg_parser_session.session`. Keep it secret — it grants full account access.

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
- **Session Profile Name** — defaults to `sessions/tg_parser_session`. Change only if managing multiple Telegram accounts.

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
├── app.py                      # NiceGUI presentation layer
├── telegram_backend/           # Backend service package
│   ├── __init__.py             # Re-exports all public symbols
│   ├── client.py               # TelegramClient creation and session management
│   ├── downloader.py           # Parallel chunk + file download, background worker
│   ├── filesystem.py           # Path helpers, sanitization, file scanning
│   ├── scanner.py              # Telegram scan/fetch functions, video metadata
│   └── state.py                # App state, queue management, recent groups
├── ui/                         # UI sub-modules (extracted from app.py)
│   ├── api_routes.py           # FastAPI /api/download-file and /api/download-folder
│   ├── helpers.py              # Pure UI helpers (formatting, grouping, merging)
│   └── theme.py                # Colour palette and global CSS
├── login.py                    # One-time Telegram authorization helper
├── pyproject.toml              # Project metadata and dependencies
├── .env.example                # Credential template — copy to .env
├── sessions/                   # Telegram session files (gitignored)
├── recent_groups.json          # Persisted recent group handles (auto-created)
├── downloads/                  # Completed files, *.part partials, *.meta.json metadata
├── .env                        # Local secrets — never commit
└── venv/                       # Local virtualenv — never commit
```

---

## Storage Semantics

All download state lives inside `downloads/`:

- `<filename>` — completed, size-verified file
- `<filename>.part` — in-progress partial download
- `<filename>.meta.json` — resume metadata (msg ID, chat, expected size, topic)
- Topic subfolders mirror the forum structure when applicable

A file is considered complete **only** when the final filename exists without a `.part` or `.meta.json` sibling.

Session files live in `sessions/` and are never committed. The `__scan` and `__download_<n>` derived slots are created automatically inside the same folder from the base session.

---

## Architecture

### `app.py` — presentation layer

Owns layout, NiceGUI wiring, scan form, download monitor, inventory rendering, and calling backend functions. Extracts theme constants, pure helpers, and API routes into `ui/`.

### `telegram_backend/` — service package

| Module | Responsibility |
|--------|---------------|
| `client.py` | `TelegramClient` creation, derived session preparation, `sessions/` directory creation |
| `scanner.py` | `fetch_forum_topics`, `fetch_topic_videos`, `fetch_group_videos`, video metadata parsing |
| `downloader.py` | Parallel chunk download, `download_selected_chunks`, `start_download_worker` |
| `filesystem.py` | Path safety (`safe_abs_path`), filename sanitization, file/metadata scanning |
| `state.py` | `create_app_state`, queue management, recent groups, `build_posts`, progress helpers |
| `__init__.py` | Re-exports all public symbols for backward-compatible `from telegram_backend import ...` |

### `ui/` — UI sub-modules

| Module | Responsibility |
|--------|---------------|
| `theme.py` | Color palette constants and global CSS string |
| `helpers.py` | Pure helper functions: `get_post_title`, `format_eta`, `checked_targets`, `merge_videos`, etc. |
| `api_routes.py` | FastAPI routes `/api/download-file` and `/api/download-folder` |

For implementation guidance aimed at coding agents, see `AGENTS.md`.

---

## Troubleshooting

**Missing credentials / zero API ID** — create or fix `.env` (copy from `.env.example`).

**Authentication needed** — run `python login.py`. Derived scan/download sessions are recreated from the base session automatically if missing.

**Search appears stale** — search is server-side. Run a new scan after changing the query.

**File is `.part` + `.meta.json` but no final file** — download was interrupted. Use Resume in the monitor.

**Downloads feel slow** — try increasing `Parallel chunks per file` (2–4). Beyond 4 risks Telegram rate-limits.

**`database is locked` error** — each parallel file download uses its own derived session slot (`__download_0`, `__download_1`, etc.). If you see this, reduce `Parallel files` to 1.

---

## Security Notes

- Never commit `.env`.
- Never commit `*.session` files — they are stored in `sessions/` which is gitignored.
- All session files (base + derived) grant full Telegram account access.
- Filenames are sanitized before use.
- Output paths are restricted to the repository root.

---

## Known Gaps

- No automated test suite yet.
- Validation is compile-check plus manual verification.

