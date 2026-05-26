# AGENTS.md

## Purpose

This file is for coding agents and automated maintainers working in this repository.

The project is a NiceGUI Telegram video downloader with a strict split between presentation logic (`app.py`) and backend operational logic (`telegram_backend.py`).

---

## Code Ownership

### `app.py` — presentation layer

It should own:

- NiceGUI layout and component wiring
- left-drawer controls (connection config, download settings sliders)
- scan form rendering and recent-groups UI
- download monitor rendering (timer-based, not page-reload-based)
- inventory rendering with topic/hashtag/resolution filters
- UI-only helpers (post title formatting, checkbox aggregation, `_format_eta`)
- calling backend functions and writing to the shared `state` dict

It must not re-implement Telegram orchestration, queue management, filesystem logic, or chunk download behavior.

### `telegram_backend.py` — service layer

It should own:

- Telegram client creation (`create_telegram_client`, `prepare_client_session`)
- derived session management — scan uses `__scan`, each parallel download slot uses `__download_0`, `__download_1`, etc.
- session-state default structures (`create_app_state`, `ensure_session_defaults`, `reset_scan_state`, `default_download_progress`)
- filesystem safety helpers (`safe_abs_path`, `sanitize_filename`)
- scan and pagination via Telethon (`fetch_forum_topics`, `fetch_topic_videos`, `fetch_group_videos`)
- queue and progress bookkeeping (`queue_downloads`, `set_task_status`, `sync_progress_summary`, `remove_from_queue`)
- resumable partial file detection (`scan_incomplete_downloads`, `filter_completed_incomplete`, `filter_visible_incomplete`)
- parallel chunk download logic (`download_video_parallel`)
- in-place partial download finalization inside `download_selected_chunks`
- background worker lifecycle (`start_download_worker`)
- recent groups persistence (`load_recent_groups`, `save_recent_groups`, `add_recent_group`, `remove_recent_group`)

If the change affects Telegram behavior, queueing, storage guarantees, resume semantics, or download orchestration, it belongs here.

---

## Critical Invariants

Do not regress these behaviors:

- Incomplete downloads must remain in `downloads/` beside their target file as `*.part` and `*.meta.json`.
- Completed files must appear only as the final filename in `downloads/` after metadata cleanup.
- Search is server-side and is passed into Telegram message iteration via `search=`.
- Pagination must continue using the same loaded search query.
- Download paths must stay inside the repository root (`_SAFE_ROOT`).
- Base and derived session files plus `.env` values must never be committed.
- Scan and download clients must use separate derived session files — scan uses `__scan`, each parallel download slot uses `__download_<n>` — to avoid SQLite `database is locked` errors.

---

## Current Runtime Model

### Scan flow

1. UI collects target group and optional search text via NiceGUI inputs.
2. Scan button fires an async handler (`_do_scan`) that runs Telegram I/O in a thread via `run.io_bound(asyncio.run, coro)`.
3. Manual scan uses the requested batch size from the UI; `Scan All` keeps paging until the backend returns no next offset.
4. For forum-enabled chats without a search query, `fetch_forum_topics(...)` loads topic summaries first, sorted by last update, using the derived scan session.
5. The UI renders those topic summaries as collapsed sections and only fetches full topic contents on demand.
6. When the user loads one topic, `fetch_topic_videos(...)` iterates that topic thread and merges all of its video messages into the current view.
7. For non-forum chats, or when a search query is present, `fetch_group_videos(...)` scans chat messages with `search=...`.
8. Message scans must still finish the current `grouped_id` album so a multi-video post is not split across pages.
9. Returned video records are grouped into posts in the UI and rendered under collapsed topic sections.
10. On successful scan completion, `add_recent_group(target)` persists the group handle to `recent_groups.json`.

### Download flow

1. UI enqueues selected videos through `queue_downloads(...)`.
2. UI starts `start_download_worker(...)` in a background thread, passing `parallel_downloads` (1–3) from the UI slider.
3. A `ui.timer` refreshes the download monitor every 0.5 seconds while a download is active.
4. Worker picks a batch of up to `parallel_downloads` queued tasks each iteration.
5. If batch size = 1, the single task runs inline (one `asyncio.run` call).
6. If batch size > 1, each task runs in its own `threading.Thread` with its own `asyncio.run` and its own slot-indexed session (`__download_0`, `__download_1`, etc.).
7. `download_selected_chunks(...)` downloads into `downloads/.../<filename>.part`.
8. `on_progress` inside `download_selected_chunks` updates `per_file_progress[str(task_id)]` with per-file bytes/speed, then aggregates into top-level `bytes_done`/`bytes_total`/`speed_mbps` on the progress state.
9. On terminal state (done / skipped / stopped / error) the task's entry is removed from `per_file_progress`.
10. Resume metadata is stored in `downloads/.../<filename>.meta.json`.
11. File is renamed to the final filename in place only after full size verification.

### Resume flow

1. `scan_incomplete_downloads(...)` discovers resume metadata in `downloads/`.
2. UI exposes resumable entries in the download monitor.
3. Resume actions requeue those items through the same worker path.

---

## State Dict Keys

Module-level `state = create_app_state()` — plain dict. Key sections:

| Key | Type | Purpose |
|-----|------|---------|
| `download_progress` | dict | Full download state — see below |
| `stop_event` | `threading.Event` | Signals background worker to stop |
| `found_videos` | list | Normalized video records from scan |
| `forum_topics` | list | Topic summaries for forum chats |
| `topic_res_filters` | dict | `{topic_id: set(resolution_str)}` — active resolution filter per topic |
| `topic_hashtag_filters` | dict | `{topic_id: set("#tag")}` — active hashtag filter per topic |
| `scan_has_more` | bool | Whether more pages are available |
| `scan_mode` | str | `"messages"` or `"topics"` |

### `download_progress` sub-keys

| Key | Purpose |
|-----|---------|
| `active` | `True` while background worker is running |
| `task_queue` | list of task dicts — each has `id`, `filename`, `size_bytes`, `chat`, `status`, `error`, etc. |
| `per_file_progress` | `{str(task_id): {bytes_done, bytes_total, speed_mbps, filename}}` — live per-file progress for parallel downloads |
| `bytes_done` / `bytes_total` | Aggregate across all currently downloading files |
| `speed_mbps` | Aggregate speed across all currently downloading files |
| `files_done` | Count of completed files this session |
| `error` | Worker-level error string or `None` |

---

## NiceGUI UI Patterns

- **Monitor refresh**: `ui.timer(0.5, _render_monitor, active=False)` — activated when a download starts, paused when idle. The monitor container is cleared and rebuilt on each tick.
- **Expansion state persistence**: mutable cell dict + `exp.on("update:model-value", lambda e: cell.__setitem__("key", bool(e.args)))` + `value=cell["key"]` in constructor. Used for `_files_state["open"]`, `_monitor_state["queue_open"]`, `_monitor_state["incomplete_open"]`.
- **Inventory rebuild**: `inventory_container.clear()` + re-render. Called after scan, topic load, or download queue change.
- **Async Telegram I/O**: `await run.io_bound(asyncio.run, coro)` keeps the NiceGUI event loop unblocked.
- **Notifications**: `ui.notify(msg, type='positive'|'negative'|'warning'|'info')`.
- **App state**: Module-level `state = create_app_state()` (plain dict). `post_selections` is a separate module-level dict for checkbox UI state.

### CRITICAL: NiceGUI async handlers

**Never use `asyncio.ensure_future()` for `on_click` or `on` handlers.** It detaches from the NiceGUI client context and causes `RuntimeError: The current slot cannot be determined`.

Correct pattern:

```python
ui.button("Scan", on_click=lambda: _do_scan(False))
```

NiceGUI awaits the returned coroutine with full client context automatically.

---

## Recent Groups

- Stored in `recent_groups.json` at project root.
- JSON array of `{"handle": str, "display_name": str, "last_used": ISO str}`.
- Max 20 entries (`MAX_RECENT_GROUPS`), newest first.
- `add_recent_group(target)` is called **only** on successful scan — groups are never removed on failure.
- Only the user can remove a group (via the ✕ chip button in the scan card).
- Backend functions: `load_recent_groups`, `save_recent_groups`, `add_recent_group`, `remove_recent_group`.
- UI: `_render_recent_groups()` closure in `app.py` renders pill chips below the scan button row.

---

## Filter System

### Resolution filters (`topic_res_filters`)

- State key: `state["topic_res_filters"]` — dict of `topic_id (int) → set of resolution strings` (e.g. `{"1080p", "720p"}`).
- Resolutions are extracted from video metadata height or filename hints via `_RESOLUTION_HINT_RE`.
- Helper functions in `app.py`: `_trf(tid)`, `_toggle_trf(tid, res)`, `_clear_trf(tid)`.

### Hashtag filters (`topic_hashtag_filters`)

- State key: `state["topic_hashtag_filters"]` — dict of `topic_id (int) → set of hashtag strings` (e.g. `{"#sport", "#news"}`).
- Hashtags extracted from `post["description"]` text via `_HASHTAG_RE = re.compile(r"#\w+")`.
- Helper functions in `app.py`: `_thf(tid)`, `_toggle_thf(tid, tag)`, `_clear_thf(tid)`, `_clear_all_filters(tid)`.

### Filter row rendering

- `_topic_filter_row(topic_id, topic_posts)` renders both rows in the topic header.
- Active filter buttons: bold, checkmark prefix, colored glow border (blue for resolution, violet for hashtag).
- Inactive buttons: dim surface background.
- A `🔍 Filters active` badge appears on the topic container when any filter is on.

Both filter dicts are reset in `_clear_scan()` and `_do_scan()`.

---

## Parallel Download Architecture

- `start_download_worker` accepts `parallel_downloads` (int, 1–3, default 1).
- Each iteration picks a batch of up to `parallel_downloads` `"queued"` tasks.
- Batch = 1: runs `asyncio.run(download_selected_chunks(..., slot=0))` inline.
- Batch > 1: each task gets `threading.Thread(target=lambda: asyncio.run(download_selected_chunks(..., slot=n)))`.
- The `slot` param causes `prepare_client_session` to derive `__download_0`, `__download_1`, etc. — separate SQLite files, no locking.
- All slot threads are `join()`ed before the next batch iteration.
- UI reads `n_active` from `sum(1 for t in queue if t["status"] == "downloading")` — not from `len(per_file_progress)`.

---

## Safe Edit Strategy

- Prefer small, localized edits.
- Start from the controlling file for the behavior you are changing.
- Validate immediately after the first substantive edit.
- Avoid mixing UI refactors with backend behavior changes unless the task requires both.
- Do not reintroduce client-side search filtering unless explicitly requested.
- Do not remove group from recent list on scan failure — only `add_recent_group` is called on success.

---

## Validation

Primary validation command:

```bash
source venv/bin/activate && python -m py_compile app.py telegram_backend.py
```

Manual validation command:

```bash
python app.py
```

Then open [http://localhost:8080](http://localhost:8080).

Use manual validation when changing:

- layout and spacing
- scan form behavior
- search handling
- queue and resume UI
- monitor behavior during active download
- download completion semantics
- filter rendering

---

## Files to Inspect First by Problem Type

| Problem | Start here |
|---------|-----------|
| UI/layout issue | `app.py` |
| Scan or search issue | `telegram_backend.py` → `fetch_group_videos` |
| Forum topics issue | `telegram_backend.py` → `fetch_forum_topics`, `fetch_topic_videos` |
| Download placement, resume, or partial-file issue | `telegram_backend.py` → `download_selected_chunks`, `start_download_worker` |
| Parallel download or session locking | `telegram_backend.py` → `start_download_worker`, `prepare_client_session` |
| Filter rendering | `app.py` → `_topic_filter_row`, `_filtered_targets`, `_render_post` |
| Recent groups | `telegram_backend.py` → `add_recent_group`; `app.py` → `_render_recent_groups` |
| Authentication | `login.py` |

---

## Known Gaps

- No automated test suite yet.
- No committed `.env.example` yet.
- Validation is primarily compile-check plus manual verification.

