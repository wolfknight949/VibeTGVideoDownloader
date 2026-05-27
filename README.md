# VibeTG — Telegram Video Downloader

A self-hosted web app that pulls videos out of Telegram groups, channels, and forum chats — faster than Telegram itself — and serves them directly to any device on your network, including iPhone and iPad.

---

## Why VibeTG?

| Problem with Telegram | VibeTG solution |
|---|---|
| Telegram caps download speed on its own clients | Downloads directly over MTProto with parallel chunk streams — saturates your real connection |
| You can only save one file at a time | Queue dozens of files; they download automatically one after another (or in parallel) |
| Downloading a whole topic or post group takes forever | Select an entire topic or post with one click and queue everything at once |
| Getting large videos onto iPhone/iPad is painful | Files land on your computer first, then you pull them over Wi-Fi right from the browser — no USB, no AirDrop, no iCloud |
| Forum channels bury content across hundreds of topics | Filter by resolution and hashtag inside each topic before you download anything |
| Interrupted downloads restart from zero | `.part` + `.meta.json` resume metadata means you pick up exactly where you left off |

---

## Requirements

- Python 3.10+
- A Telegram account
- API credentials from [my.telegram.org/apps](https://my.telegram.org/apps) (free, takes 2 minutes)

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/your-username/TelegramVideoDownloader.git
cd TelegramVideoDownloader
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -e .
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials from [my.telegram.org/apps](https://my.telegram.org/apps):

```env
TG_API_ID=12345678
TG_API_HASH=abcdef1234567890abcdef1234567890
```

### 3. Log in to Telegram (one-time)

```bash
python login.py
```

Follow the prompts — enter your phone number and the code Telegram sends you. This creates a session file in `sessions/`. You only need to do this once.

### 4. Start the app

```bash
python app.py
```

Open **[http://localhost:8080](http://localhost:8080)** in your browser. That's it.

---

## Core Workflow

### Step 1 — Point it at a group or channel

Paste a username (`@channelname`), an invite link, or any chat reference into **Target Group** and click **Scan Group**.

- Use **Scan All** to walk through the entire message history automatically.
- Add a keyword in **Search Messages** to use Telegram's server-side search — useful for large channels.
- Previously scanned groups are saved as chips for one-click access.

### Step 2 — Browse and filter

For forum chats, results are grouped under collapsible topic sections. Inside each topic you can:

- Filter by **video resolution** (blue buttons) — e.g. show only 1080p files.
- Filter by **hashtag** (purple buttons) — e.g. show only posts tagged `#tutorial`.
- Active filters are highlighted; a **Filters active** badge appears on the topic header.

### Step 3 — Select and queue

- Tick individual videos, or tick a whole post to select every file in it.
- Click **Download Selected** — everything goes into the download queue.
- The queue processes automatically. You can add more items while downloads are running.

### Step 4 — Transfer to iPhone or iPad over Wi-Fi

Once a file (or an entire folder/topic) is on your computer, open VibeTG from **your phone's browser** using your computer's local IP — e.g. `http://192.168.1.10:8080`. Every completed file shows a download button. Tap it and the video saves straight to your camera roll or Files app.

> **Why this is faster than AirDrop or Telegram:** The file is already fully on your computer. Your phone pulls it over local Wi-Fi at full LAN speed — typically 30–80 MB/s, far faster than anything Telegram's servers deliver.

To download a whole topic as a single ZIP, use the folder download button next to any topic header.

---

## Download Settings

All settings live in the **left drawer** under *Download Settings*.

| Setting | What it does | Recommended |
|---|---|---|
| Downloads Folder | Where files are saved | Leave as `downloads` |
| Parallel chunks per file | MTProto streams per file | 2–4 |
| Parallel files | Files downloading at the same time | 1–2 |

**Parallel chunks** is the biggest speed lever. Each chunk is an independent MTProto stream — more streams means higher throughput up to Telegram's per-account rate limit. Beyond 4 you risk hitting that limit and seeing speeds drop.

**Parallel files** lets you download multiple different files simultaneously. Each uses its own derived session slot to avoid database conflicts.

---

## Download Monitor

The monitor strip at the top of the page shows:

- Current filename (or "N files downloading in parallel")
- Aggregate speed and ETA
- A progress bar
- Per-file `XX% · Y.Y MB/s` in the queue list

**Stop** — halts the worker gracefully. Partial files are kept with their resume metadata.  
**Resume All** — re-queues every interrupted download found in the `downloads/` folder.

---

## Resumable Downloads

Every download writes two sidecar files alongside the actual content:

- `<filename>.part` — the partial file data
- `<filename>.meta.json` — message ID, chat, expected size, topic path

If the app closes, your connection drops, or you hit Stop, the next Resume picks up from the last completed byte. No re-downloading from the start.

---

## Project Layout

```
TelegramVideoDownloader/
├── app.py                    # NiceGUI UI — layout, scan form, download monitor
├── telegram_backend/         # All backend logic
│   ├── client.py             # Session management
│   ├── scanner.py            # Telegram scan and metadata parsing
│   ├── downloader.py         # Parallel chunk + file download engine
│   ├── filesystem.py         # Path helpers and filename sanitization
│   └── state.py              # App state, queue, recent groups
├── ui/
│   ├── api_routes.py         # /api/download-file and /api/download-folder endpoints
│   ├── helpers.py            # Formatting and grouping utilities
│   └── theme.py              # Color palette and global CSS
├── login.py                  # One-time authorization helper
├── pyproject.toml            # Dependencies
├── .env.example              # Credential template
├── sessions/                 # Session files (gitignored — never share these)
├── downloads/                # Your downloaded files
└── recent_groups.json        # Saved group handles (auto-managed)
```

---

## Troubleshooting

**"Missing credentials" or zero API ID**  
Create or fix your `.env` file. Copy from `.env.example` and fill in your values from [my.telegram.org/apps](https://my.telegram.org/apps).

**"Authentication needed"**  
Run `python login.py`. Derived scan/download sessions are recreated from the base session automatically if missing.

**Search results look stale**  
Search is processed by Telegram's servers, not locally. Run a new scan after changing the query.

**File stuck as `.part` with no final file**  
The download was interrupted. Click **Resume All** in the monitor.

**Downloads are slow**  
Increase *Parallel chunks per file* to 3 or 4. Also check that nothing else is saturating your connection.

**`database is locked` error**  
Reduce *Parallel files* to 1. Each parallel file needs its own session slot; the error means two workers collided on the same slot.

**Can't reach the app from my phone**  
Make sure your phone and computer are on the same Wi-Fi network. Find your computer's local IP (`ifconfig` on Mac/Linux, `ipconfig` on Windows) and open `http://<that-ip>:8080` on your phone.

---

## Security Notes

- Never commit `.env` — it contains your Telegram API credentials.
- Never commit `sessions/*.session` — these files grant full access to your Telegram account.
- Both directories are gitignored by default.
- Filenames are sanitized before writing to disk.
- File serving is restricted to the `downloads/` folder — no path traversal is possible.
