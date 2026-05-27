import asyncio
import os
import urllib.parse
import warnings

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import ui.api_routes  # noqa: F401 — registers /api/* routes
from dotenv import load_dotenv
from nicegui import run, ui

from telegram_backend import (
    DEFAULT_DOWNLOADS_DIR,
    TASK_STATUS_ICONS,
    add_recent_group,
    apply_downloaded_flags,
    build_posts,
    create_app_state,
    delete_downloaded_file,
    fetch_forum_topics,
    fetch_group_videos,
    fetch_topic_videos,
    filter_completed_incomplete,
    filter_visible_incomplete,
    list_downloaded_files,
    load_recent_groups,
    queue_downloads,
    remove_from_queue,
    remove_recent_group,
    reset_scan_state,
    safe_abs_path,
    scan_downloaded_files,
    requeue_task,
    scan_incomplete_downloads,
    start_download_worker,
    is_system_file,
)
from ui.helpers import (
    checked_targets as _checked_targets,
    extract_hashtags as _extract_hashtags,
    format_eta as _format_eta,
    get_post_title as _get_post_title,
    group_by_topic as _group_by_topic,
    has_cursor as _has_cursor,
    mark_loaded as _mark_loaded,
    merge_topics as _merge_topics,
    merge_videos as _merge_videos,
    topic_label_of as _topic_label_of,
    topic_targets as _topic_targets,
)
from ui.theme import (
    BG as _BG,
    BLUE as _BLUE,
    BORDER as _BORDER,
    CARD as _CARD,
    CSS as _CSS,
    DIM as _DIM,
    GREEN as _GREEN,
    MUTED as _MUTED,
    ORANGE as _ORANGE,
    PURPLE as _PURPLE,
    RED as _RED,
    SURFACE as _SURFACE,
    TEXT as _TEXT,
    YELLOW as _YELLOW,
)

load_dotenv()

_ENV_API_ID  = int(os.environ.get("TG_API_ID", "0"))
_ENV_API_HASH = os.environ.get("TG_API_HASH", "")

state = create_app_state()
post_selections: dict = {}
expanded_topics: set = set()  # topic_ids currently expanded in the UI


# ── Page ─────────────────────────────────────────────────────────────────────

@ui.page("/")
def main_page() -> None:
    ui.colors(
        primary=_BLUE, secondary=_GREEN, accent=_PURPLE,
        positive=_GREEN, negative=_RED, warning=_YELLOW, info=_BLUE,
    )
    ui.add_css(_CSS)

    # ── App header ──────────────────────────────────────────────────────────
    with ui.header().classes("items-center gap-3 q-px-md").style("min-height:52px;"):
        menu_btn = ui.button(icon="menu").props("flat round dense").style(f"color:{_MUTED}")
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("movie", size="22px").style(f"color:{_BLUE}")
            ui.label("VibeTG").style(f"font-size:1.05rem; font-weight:700; color:{_TEXT}")
            ui.label("Video Downloader").style(f"font-size:0.72rem; color:{_DIM}; margin-top:2px;")
        ui.space()
        header_status = ui.html(
            f'<span style="font-size:.75rem; color:{_DIM};">Idle</span>',
            sanitize=False,
        )

    # ── Settings drawer ─────────────────────────────────────────────────────
    with ui.left_drawer(value=False, bordered=True).classes("q-pa-lg").style(
        "background:#010409; border-right:1px solid #21262d; width:280px;"
    ) as drawer:
        menu_btn.on("click", drawer.toggle)

        with ui.row().classes("items-center gap-2 q-mb-md"):
            ui.icon("tune", size="20px").style(f"color:{_BLUE}")
            ui.label("Settings").style(f"font-size:1rem; font-weight:600; color:{_TEXT}")

        ui.label("Connection").classes("section-lbl q-mt-sm")
        ui.separator().style(f"background:{_BORDER}; margin:4px 0 10px;")

        if _ENV_API_ID and _ENV_API_HASH:
            with ui.row().classes("items-center gap-1 q-mb-sm"):
                ui.icon("verified", size="14px").style(f"color:{_GREEN}")
                ui.label(".env credentials loaded").style(f"font-size:.72rem; color:{_GREEN};")

        api_id_input = (
            ui.number("API ID", value=_ENV_API_ID, precision=0)
            .props("outlined dense dark")
            .classes("w-full q-mb-sm")
        )
        api_hash_input = (
            ui.input("API Hash", value=_ENV_API_HASH, password=True, password_toggle_button=True)
            .props("outlined dense dark")
            .classes("w-full q-mb-sm")
        )
        session_input = (
            ui.input("Session name", value="sessions/tg_parser_session")
            .props("outlined dense dark")
            .classes("w-full")
        )

        ui.label("Downloads").classes("section-lbl q-mt-lg")
        ui.separator().style(f"background:{_BORDER}; margin:4px 0 10px;")

        downloads_input = (
            ui.input("Output folder", value=DEFAULT_DOWNLOADS_DIR)
            .props("outlined dense dark")
            .classes("w-full q-mb-md")
        )

        chunks_val = ui.label("Parallel chunks: 4").style(
            f"font-size:.72rem; color:{_MUTED}; margin-bottom:6px;"
        )
        chunks_slider = (
            ui.slider(min=1, max=8, value=4)
            .props("dark label")
            .classes("w-full")
        )
        chunks_slider.on(
            "update:model-value",
            lambda e: chunks_val.set_text(f"Parallel chunks: {int(e.args)}"),
        )

        parallel_val = ui.label("Parallel files: 1").style(
            f"font-size:.72rem; color:{_MUTED}; margin-bottom:6px; margin-top:10px;"
        )
        parallel_slider = (
            ui.slider(min=1, max=3, value=1)
            .props("dark label")
            .classes("w-full q-mb-md")
        )
        parallel_slider.on(
            "update:model-value",
            lambda e: parallel_val.set_text(f"Parallel files: {int(e.args)}"),
        )

        ui.separator().style(f"background:{_BORDER}; margin:20px 0 12px;")
        ui.label("Run `python login.py` once to authorise your Telegram account.").style(
            f"font-size:.7rem; color:{_DIM}; line-height:1.5;"
        )

    # ── Accessors ────────────────────────────────────────────────────────────
    def _aid()     -> int:  return int(api_id_input.value or 0)
    def _ahash()   -> str:  return (api_hash_input.value or "").strip()
    def _sess()    -> str:  return (session_input.value or "sessions/tg_parser_session").strip()
    def _dlpath()  -> str:  return (downloads_input.value or DEFAULT_DOWNLOADS_DIR).strip()
    def _workers() -> int:  return int(chunks_slider.value)
    def _parallel_dl() -> int: return int(parallel_slider.value)

    # ── Main content ─────────────────────────────────────────────────────────
    with ui.column().classes("w-full q-pa-lg gap-5"):


        # ── Downloaded files browser ─────────────────────────────────────────
        files_wrap = ui.column().classes("w-full gap-2")
        _files_state: dict = {"open": False, "snapshot": None}  # mutable cell

        def _render_files() -> None:
            dp = _dlpath()
            files = list_downloaded_files(dp)

            # Build a lightweight snapshot to detect real changes
            snapshot = [(f["rel_path"], f["size_mb"]) for f in files]
            if snapshot == _files_state["snapshot"]:
                return  # nothing changed — don't touch the DOM
            _files_state["snapshot"] = snapshot

            files_wrap.clear()
            if not files:
                return

            total_mb = sum(f["size_mb"] for f in files)

            with files_wrap:
                with ui.expansion(
                    f"Downloaded Files  ·  {len(files)} file{'s' if len(files) != 1 else ''}  ·  {total_mb:.0f} MB",
                    icon="folder",
                    value=_files_state["open"],
                ).classes("w-full") as _files_exp:
                    _files_exp.on(
                        "update:model-value",
                        lambda e: _files_state.__setitem__("open", bool(e.args)),
                    )
                    with ui.column().classes("gap-0 q-pa-sm"):
                        # Group by folder
                        folders: dict = {}
                        for f in files:
                            folders.setdefault(f["folder"], []).append(f)

                        for folder, folder_files in sorted(folders.items(), key=lambda x: x[0].lower()):
                            folder_mb = sum(f["size_mb"] for f in folder_files)
                            if folder:
                                with ui.row().classes("items-center gap-1 q-mt-xs q-mb-xs"):
                                    ui.icon("folder_open", size="14px").style(f"color:{_ORANGE}")
                                    ui.label(folder).style(
                                        f"font-size:.73rem; font-weight:600; color:{_ORANGE};"
                                    )
                                    ui.label(f"{len(folder_files)} files · {folder_mb:.0f} MB").style(
                                        f"font-size:.68rem; color:{_DIM};"
                                    )

                            for fitem in folder_files:
                                indent = "padding-left:22px;" if folder else ""
                                _has_fthumb = bool(fitem.get("msg_id") and fitem.get("chat"))
                                _frow_cls = (
                                    "w-full items-center gap-2 video-row-thumb"
                                    if _has_fthumb else
                                    "w-full items-center gap-2 video-row"
                                )
                                with ui.row().classes(_frow_cls).style(indent):
                                    if _has_fthumb:
                                        _fq = urllib.parse.quote(fitem["chat"])
                                        _sq = urllib.parse.quote(_sess())
                                        (
                                            ui.image(
                                                f"/api/thumbnail?msg_id={fitem['msg_id']}"
                                                f"&chat={_fq}&session={_sq}"
                                            )
                                            .props("no-spinner fit=cover")
                                            .style(
                                                "width:72px; min-width:72px; height:45px;"
                                                " border-radius:3px; flex-shrink:0;"
                                                f" background:{_CARD};"
                                            )
                                        )
                                    else:
                                        ui.icon("movie", size="13px").style(f"color:{_DIM}")
                                    ui.label(fitem["filename"]).style(
                                        f"font-family:monospace; font-size:.75rem; color:{_TEXT}; flex:1;"
                                        "overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                                    )
                                    ui.label(f"{fitem['size_mb']} MB").style(
                                        f"font-size:.68rem; color:{_DIM}; white-space:nowrap; flex-shrink:0;"
                                    )
                                    dl_url = (
                                        f"/api/download-file"
                                        f"?rel_path={urllib.parse.quote(fitem['rel_path'])}"
                                        f"&dl_dir={urllib.parse.quote(dp)}"
                                    )
                                    ui.button(
                                        icon="download",
                                        on_click=lambda url=dl_url: ui.download(url),
                                    ).props("flat round dense").tooltip("Save to device").style(
                                        f"color:{_BLUE}; width:28px; flex-shrink:0;"
                                    )
                                    ui.button(
                                        icon="delete_outline",
                                        on_click=lambda fi=fitem: _delete_file(fi),
                                    ).props("flat round dense").tooltip("Delete file").style(
                                        f"color:{_RED}; width:28px; flex-shrink:0;"
                                    )

        def _delete_file(fitem: dict) -> None:
            if delete_downloaded_file(fitem["abs_path"]):
                ui.notify(f"Deleted {fitem['filename']}", type="warning", position="top-right", timeout=3000)
            else:
                ui.notify("Could not delete file.", type="negative", position="top-right")
            _files_state["snapshot"] = None  # force rebuild after deletion
            _render_files()

        _render_files()
        ui.timer(3.0, _render_files)

        # ── Scan form ───────────────────────────────────────────────────────
        with ui.card().classes("w-full q-pa-md"):
            with ui.row().classes("items-center gap-2 q-mb-sm"):
                ui.icon("manage_search", size="18px").style(f"color:{_BLUE}")
                ui.label("Scan").style(f"font-size:.85rem; font-weight:600; color:{_TEXT}")

            with ui.row().classes("w-full items-end gap-3 flex-wrap"):
                target_input = (
                    ui.input(placeholder="@username, t.me/link, or chat ID")
                    .props("outlined dense dark")
                    .classes("col-grow")
                    .style("min-width:180px;")
                )
                target_input.props('label="Group / Channel"')
                target_input.on("keydown.enter", lambda: _do_scan(False))

                search_input = (
                    ui.input(placeholder="Filename or keyword (optional)")
                    .props("outlined dense dark")
                    .classes("col-grow")
                    .style("min-width:160px;")
                )
                search_input.props('label="Search"')
                search_input.on("keydown.enter", lambda: _do_scan(False))

                batch_input = (
                    ui.number(value=50, min=1, max=500, precision=0)
                    .props("outlined dense dark")
                    .style("width:90px;")
                )
                batch_input.props('label="Batch"')

                scan_btn = ui.button(
                    "Scan topics", icon="search",
                    on_click=lambda: _do_scan(False),
                ).props("unelevated").style(
                    f"background:{_BLUE}; color:#fff; font-weight:600; min-height:36px;"
                ).tooltip("Fetch one batch of topics (Batch size)")
                scan_all_btn = ui.button(
                    "Scan All", icon="cloud_sync",
                    on_click=lambda: _do_scan(True),
                ).props("unelevated").style(
                    f"background:{_SURFACE}; color:{_TEXT}; border:1px solid {_BORDER};"
                    f" font-weight:600; min-height:36px;"
                ).tooltip("Fetch all topics at once")
                ui.button(
                    icon="close",
                    on_click=lambda: _clear_scan(),
                ).props("flat round dense").style(
                    f"color:{_DIM};"
                ).tooltip("Clear scan")

            # ── Recent groups ────────────────────────────────────────────────
            recent_grp_wrap = ui.row().classes("items-center gap-2 flex-wrap q-mt-xs")

        # ── Recent-group helpers ─────────────────────────────────────────────
        def _render_recent_groups() -> None:
            recent_grp_wrap.clear()
            groups = load_recent_groups()
            if not groups:
                return
            with recent_grp_wrap:
                ui.icon("history", size="14px").style(f"color:{_DIM}")
                for g in groups:
                    handle  = g.get("handle", "")
                    label   = g.get("display_name") or handle
                    with ui.row().classes("items-center gap-0 no-wrap").style(
                        f"background:{_SURFACE}; border:1px solid {_BORDER};"
                        " border-radius:14px; padding:0 4px 0 10px; height:26px;"
                    ):
                        ui.label(label).style(
                            f"font-size:.72rem; color:{_TEXT}; cursor:pointer; white-space:nowrap;"
                        ).on("click", lambda h=handle: (
                            target_input.set_value(h),
                        ))
                        ui.button(
                            icon="close",
                            on_click=lambda h=handle: (
                                remove_recent_group(h),
                                _render_recent_groups(),
                            ),
                        ).props("flat round dense").style(
                            f"color:{_DIM}; width:20px; height:20px; font-size:.6rem;"
                        )

        _render_recent_groups()

        # ── Download monitor ────────────────────────────────────────────────
        monitor_wrap = ui.column().classes("w-full gap-2")
        _monitor_state: dict = {"queue_open": True, "queue_sig": None}

        with monitor_wrap:
            active_strip_wrap = ui.column().classes("w-full")
            queue_wrap        = ui.column().classes("w-full")

        def _upd_header(active: bool, spd: float) -> None:
            if active:
                header_status.content = (
                    f'<span style="font-size:.75rem; color:{_GREEN};">'
                    f'● Downloading &nbsp; ⚡ {spd:.1f} MB/s</span>'
                )
            else:
                header_status.content = (
                    f'<span style="font-size:.75rem; color:{_DIM};">Idle</span>'
                )

        def _render_active_strip() -> None:
            """Rebuild only the live progress strip — called every 0.5 s."""
            p      = state["download_progress"]
            active = p.get("active", False)
            spd    = p.get("speed_mbps", 0.0)
            queue  = p.get("task_queue", [])
            _upd_header(active, spd)
            active_strip_wrap.clear()
            if not active:
                return
            bd       = p.get("bytes_done", 0)
            bt       = max(p.get("bytes_total", 1), 1)
            fn       = p.get("filename", "…")
            fi       = p.get("file_idx", 0)
            ft       = max(p.get("total_files", 1), 1)
            cd       = p.get("chunks_done", 0)
            ct       = max(p.get("chunks_total", 1), 1)
            eta      = max(bt - bd, 0) / (spd * 1048576) if spd > 0 else None
            n_active = sum(1 for t in queue if t.get("status") == "downloading")
            with active_strip_wrap:
                with ui.element("div").classes("dl-strip"):
                    with ui.row().classes("w-full items-center gap-2 q-mb-sm"):
                        ui.icon("downloading", size="16px").style(f"color:{_BLUE}")
                        if n_active > 1:
                            ui.label(f"{n_active} files downloading in parallel").style(
                                f"font-size:.82rem; font-weight:500; color:{_TEXT}; flex:1;"
                            )
                        else:
                            ui.label(fn).style(
                                f"font-size:.82rem; font-weight:500; color:{_TEXT}; flex:1;"
                                "overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                            )
                        ui.label(f"File {fi+1}/{ft}").style(
                            f"font-size:.72rem; color:{_MUTED}; white-space:nowrap;"
                        )
                        ui.label(f"⚡ {spd:.2f} MB/s").style(
                            f"font-size:.72rem; color:{_ORANGE}; white-space:nowrap;"
                        )
                        ui.label(f"ETA {_format_eta(eta)}").style(
                            f"font-size:.72rem; color:{_MUTED}; white-space:nowrap;"
                        )
                        ui.button(
                            "Stop", icon="stop",
                            on_click=_do_stop, color="negative",
                        ).props("unelevated dense").style("min-height:28px; font-size:.72rem;")
                    pct_f = bd / bt
                    ui.label(
                        f"{bd/1048576:.1f} / {bt/1048576:.1f} MB  ({pct_f*100:.0f}%)"
                    ).style(f"font-size:.68rem; color:{_MUTED}; margin-bottom:3px;")
                    ui.linear_progress(pct_f, color="blue-5", size="6px", show_value=False).classes("w-full q-mb-xs")
                    if n_active <= 1:
                        pct_b = cd / ct if ct else 0
                        ui.label(f"Chunks {cd}/{ct}").style(
                            f"font-size:.68rem; color:{_MUTED}; margin-bottom:3px;"
                        )
                        ui.linear_progress(pct_b, color="purple-4", size="4px", show_value=False).classes("w-full")

        def _render_queue_list() -> None:
            """Rebuild the queue + orphaned panel — called only when task statuses change."""
            dp       = _dlpath()
            p        = state["download_progress"]
            active   = p.get("active", False)
            dl_files = scan_downloaded_files(dp)
            all_inc  = filter_completed_incomplete(scan_incomplete_downloads(dp), dl_files)
            queue    = p.get("task_queue", [])
            done_cnt = p.get("files_done", 0)

            queue_ids = {task.get("id") for task in queue}
            inc_by_id = {i["msg_id"]: i for i in all_inc}
            orphaned  = [i for i in all_inc if i.get("msg_id") not in queue_ids]

            queue_wrap.clear()
            with queue_wrap:
                if not active and done_cnt > 0 and not p.get("error"):
                    with ui.row().classes("items-center gap-2").style(
                        f"padding:8px 12px; background:{_SURFACE};"
                        f"border:1px solid rgba(63,185,80,.2); border-radius:8px;"
                    ):
                        ui.icon("check_circle", size="16px").style(f"color:{_GREEN}")
                        ui.label(
                            f"Done — {done_cnt} file{'s' if done_cnt != 1 else ''} saved to /{dp}"
                        ).style(f"font-size:.82rem; color:{_GREEN};")

                if p.get("error"):
                    with ui.row().classes("items-center gap-2").style(
                        f"padding:8px 12px; background:{_SURFACE};"
                        f"border:1px solid rgba(248,81,73,.2); border-radius:8px;"
                    ):
                        ui.icon("error_outline", size="16px").style(f"color:{_RED}")
                        ui.label(p["error"]).style(f"font-size:.82rem; color:{_RED};")

                display_items = list(queue) + [
                    {
                        "id": i["msg_id"], "filename": i["filename"],
                        "size_bytes": i.get("size_bytes") or i.get("total_size", 0),
                        "size_mb": i.get("size_mb", 0), "date": i.get("date", ""),
                        "chat": i.get("chat", ""), "topic_id": i.get("topic_id", 0),
                        "topic_name": i.get("topic_name", ""),
                        "status": "orphaned", "_inc": i,
                    }
                    for i in orphaned
                ]

                if not display_items:
                    return

                o_count = len(orphaned)
                header_label = (
                    f"Queue  ({len(queue)} in queue, {o_count} from prev. session)"
                    if o_count else f"Queue  ({len(queue)} files)"
                )

                with ui.expansion(
                    header_label,
                    value=_monitor_state["queue_open"],
                ).classes("w-full") as _q_exp:
                    _q_exp.on("update:model-value",
                              lambda e: _monitor_state.__setitem__("queue_open", bool(e.args)))
                    with ui.column().classes("gap-1 q-pa-sm"):
                        stopped_tasks = [t for t in queue if t.get("status") == "stopped"]
                        if stopped_tasks and not active:
                            with ui.row().classes("items-center gap-2 q-mb-xs"):
                                ui.button(
                                    "Resume all stopped", icon="play_arrow",
                                    on_click=lambda: _resume_all_stopped(),
                                ).props("unelevated dense").style(
                                    f"background:{_SURFACE}; color:{_GREEN};"
                                    "border:1px solid rgba(63,185,80,.3); font-size:.75rem;"
                                )

                        pfp = p.get("per_file_progress", {})
                        for task in display_items:
                            tst  = task.get("status", "queued")
                            c    = {
                                "done": _GREEN, "downloading": _BLUE, "error": _RED,
                                "stopped": _YELLOW, "skipped": _MUTED, "orphaned": _ORANGE,
                            }.get(tst, _MUTED)
                            icon = {"orphaned": "⚠️", **TASK_STATUS_ICONS}.get(tst, "•")
                            chat = task.get("chat", "")
                            tid  = task.get("id")

                            with ui.row().classes("items-center gap-2"):
                                if chat and tid:
                                    _tq  = urllib.parse.quote(chat)
                                    _tsq = urllib.parse.quote(_sess())
                                    ui.html(
                                        f'<img src="/api/thumbnail?msg_id={tid}'
                                        f'&chat={_tq}&session={_tsq}"'
                                        f' style="width:44px;min-width:44px;height:28px;'
                                        f'border-radius:3px;flex-shrink:0;object-fit:cover;'
                                        f'background:{_CARD};"'
                                        f' onerror="this.style.display=\'none\'">',
                                        sanitize=False,
                                    )
                                ui.label(icon).style(
                                    f"color:{c}; font-size:.82rem; width:16px; flex-shrink:0;"
                                )
                                ui.label(task["filename"]).style(
                                    f"color:{_TEXT}; font-size:.78rem; flex:1;"
                                    "overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                                )

                                if tst == "downloading":
                                    fp = pfp.get(str(tid or ""), {})
                                    if fp and fp.get("bytes_total", 0) > 0:
                                        pct   = fp["bytes_done"] / fp["bytes_total"] * 100
                                        spd_f = fp.get("speed_mbps", 0.0)
                                        ui.label(f"{pct:.0f}% · {spd_f:.1f} MB/s").style(
                                            f"color:{_BLUE}; font-size:.7rem; white-space:nowrap;"
                                        )
                                    else:
                                        ui.label("starting…").style(
                                            f"color:{_MUTED}; font-size:.7rem; white-space:nowrap;"
                                        )

                                elif tst == "stopped":
                                    inc    = inc_by_id.get(tid, {})
                                    dl_mb  = round(inc.get("bytes_downloaded", 0) / 1048576, 1)
                                    tot_mb = task.get("size_mb", 0)
                                    ui.label(f"{dl_mb}/{tot_mb} MB").style(
                                        f"color:{_YELLOW}; font-size:.7rem; white-space:nowrap;"
                                    )
                                    ui.button(icon="play_arrow",
                                              on_click=lambda t=task: _resume_stopped(t)
                                    ).props("flat round dense").tooltip("Resume").style(f"color:{_GREEN}")
                                    ui.button(icon="delete_outline",
                                              on_click=lambda t=task: _delete_task_and_partial(t, dp)
                                    ).props("flat round dense").tooltip("Remove and delete partial file").style(
                                        f"color:{_RED}"
                                    )

                                elif tst == "error":
                                    ui.label((task.get("error") or "error")[:30]).style(
                                        f"color:{_RED}; font-size:.65rem; white-space:nowrap;"
                                        "max-width:120px; overflow:hidden; text-overflow:ellipsis;"
                                    )
                                    ui.button(icon="replay",
                                              on_click=lambda t=task: _resume_stopped(t)
                                    ).props("flat round dense").tooltip("Retry").style(f"color:{_ORANGE}")
                                    ui.button(icon="close",
                                              on_click=lambda t=task: (
                                                  remove_from_queue(state["download_progress"], t["id"]),
                                                  _render_queue_list(),
                                              )
                                    ).props("flat round dense").tooltip("Remove from queue").style(
                                        f"color:{_DIM}; width:24px;"
                                    )

                                elif tst == "orphaned":
                                    inc_item = task["_inc"]
                                    dl_mb  = round(inc_item.get("bytes_downloaded", 0) / 1048576, 1)
                                    tot_mb = task.get("size_mb", 0)
                                    ui.label(f"{dl_mb}/{tot_mb} MB").style(
                                        f"color:{_ORANGE}; font-size:.7rem; white-space:nowrap;"
                                    )
                                    ui.button(icon="play_arrow",
                                              on_click=lambda i=inc_item: _resume_orphaned(i, dl_files)
                                    ).props("flat round dense").tooltip("Resume download").style(
                                        f"color:{_GREEN}"
                                    )
                                    ui.button(icon="delete_outline",
                                              on_click=lambda i=inc_item: _delete_inc(i)
                                    ).props("flat round dense").tooltip("Delete partial file").style(
                                        f"color:{_RED}"
                                    )

                                elif tst == "queued":
                                    ui.label(f"{task.get('size_mb', 0)} MB").style(
                                        f"color:{_DIM}; font-size:.7rem; white-space:nowrap;"
                                    )
                                    ui.button(icon="close",
                                              on_click=lambda t=task: (
                                                  remove_from_queue(state["download_progress"], t["id"]),
                                                  _render_queue_list(),
                                              )
                                    ).props("flat round dense").tooltip("Remove from queue").style(
                                        f"color:{_DIM}; width:24px;"
                                    )

                                else:  # done, skipped
                                    ui.label(f"{task.get('size_mb', 0)} MB").style(
                                        f"color:{_DIM}; font-size:.7rem; white-space:nowrap;"
                                    )

        def _render_monitor() -> None:
            """Timer callback: refresh active strip every tick; queue only on state change."""
            p   = state["download_progress"]
            _render_active_strip()
            sig = (
                tuple(
                    (t.get("id"), t.get("status"), t.get("error", ""))
                    for t in p.get("task_queue", [])
                ),
                p.get("error"),
                p.get("files_done", 0),
                p.get("active", False),
            )
            if sig != _monitor_state["queue_sig"]:
                _monitor_state["queue_sig"] = sig
                _render_queue_list()

        def _do_stop() -> None:
            state["stop_event"].set()
            ui.notify("Stop signal sent.", type="warning", position="top-right", timeout=3000)
            _render_active_strip()

        def _start_worker() -> None:
            state["stop_event"].clear()
            start_download_worker(
                _aid(), _ahash(), _sess(), _dlpath(), _dlpath(),
                _workers(), state["stop_event"], state["download_progress"],
                parallel_downloads=_parallel_dl(),
            )
            monitor_timer.active = True

        def _resume_stopped(task: dict) -> None:
            if requeue_task(state["download_progress"], task["id"]):
                _start_worker()
            _render_queue_list()

        def _resume_all_stopped() -> None:
            dp = state["download_progress"]
            for task in list(dp.get("task_queue", [])):
                if task.get("status") == "stopped":
                    requeue_task(dp, task["id"])
            _start_worker()
            _render_queue_list()

        def _delete_task_and_partial(task: dict, dl_dir: str) -> None:
            remove_from_queue(state["download_progress"], task["id"])
            rel = task.get("download_rel_path") or task.get("filename", "")
            try:
                abs_dir = safe_abs_path(dl_dir)
                for suffix in (".part", ".meta.json"):
                    p_path = os.path.join(abs_dir, rel + suffix)
                    if os.path.exists(p_path):
                        os.remove(p_path)
            except OSError:
                pass
            _render_queue_list()

        def _resume_orphaned(inc: dict, dl_files_set) -> None:
            r = queue_downloads(
                state["download_progress"],
                [{
                    "id": inc["msg_id"], "filename": inc["filename"],
                    "size_bytes": inc.get("size_bytes") or inc.get("total_size", 0),
                    "size_mb": inc.get("size_mb", 0), "date": inc.get("date", ""),
                    "chat": inc.get("chat", ""), "topic_id": inc.get("topic_id", 0),
                    "topic_name": inc.get("topic_name", ""),
                }],
                downloaded_files=dl_files_set,
            )
            if r["added"]:
                _start_worker()
            elif r["already_downloaded"]:
                ui.notify("Already downloaded.", type="info", position="top-right")
            _render_queue_list()

        def _delete_inc(inc: dict) -> None:
            for path in (inc.get("_part_path", ""), inc.get("_meta_path", "")):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            _render_queue_list()

        _render_monitor()
        monitor_timer = ui.timer(
            0.5, _render_monitor,
            active=bool(state["download_progress"].get("active")),
        )


        # ── Inventory ───────────────────────────────────────────────────────
        inv_wrap = ui.column().classes("w-full gap-2")

        def _launch(targets: list) -> None:
            dp = _dlpath()
            r  = queue_downloads(
                state["download_progress"], targets,
                default_chat=(target_input.value or "").strip(),
                downloaded_files=scan_downloaded_files(dp),
            )
            if r["added"]:
                _start_worker(); _render_monitor(); _render_inv()
            elif r["already_downloaded"]:
                ui.notify("Already downloaded.", type="info", position="top-right")

        def _render_inv() -> None:
            inv_wrap.clear()
            dp           = _dlpath()
            dl_files     = scan_downloaded_files(dp)
            videos       = state.get("found_videos", [])
            forum_topics = state.get("forum_topics", [])
            scan_mode    = state.get("scan_mode", "messages")
            loaded_grp   = state.get("scan_loaded_group", "")
            loaded_q     = state.get("scan_loaded_search_query", "")

            with inv_wrap:
                if not videos and not forum_topics:
                    with ui.row().classes("items-center gap-2").style(
                        f"padding:20px; background:{_CARD}; border:1px solid {_BORDER}; border-radius:10px;"
                    ):
                        ui.icon("inbox", size="20px").style(f"color:{_DIM}")
                        if loaded_grp:
                            q_part = f' matching "{loaded_q}"' if loaded_q else ""
                            ui.label(f"No videos found in {loaded_grp}{q_part}.").style(
                                f"font-size:.85rem; color:{_MUTED};"
                            )
                        else:
                            ui.label("Enter a group above and click Scan to find videos.").style(
                                f"font-size:.85rem; color:{_MUTED};"
                            )
                    return

                posts        = build_posts(videos)
                apply_downloaded_flags(posts, dl_files)
                all_v        = [v for p in posts.values() for v in p["videos"]]
                sel          = _checked_targets(posts, post_selections)
                total        = sum(v["size_mb"] for v in all_v)
                sel_mb       = sum(v["size_mb"] for v in sel)
                loading_more = state.get("loading_more", False)

                # Toolbar
                with ui.column().classes("w-full gap-0").style(
                    f"padding:10px 14px; background:{_CARD}; border:1px solid {_BORDER};"
                    "border-radius:10px;"
                ):
                    # Stats + actions row
                    with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                        if scan_mode == "forum_topics" and forum_topics:
                            lc = sum(1 for t in forum_topics if t.get("loaded"))
                            ul = len(forum_topics) - lc
                            _chip(f"{len(forum_topics)} topics")
                            if lc:
                                _chip(f"{lc} loaded", "green")
                            if ul:
                                _chip(f"{ul} unloaded")
                            _chip(f"{len(posts)} posts")
                            _chip(f"{len(all_v)} videos")
                            _chip(f"{total:.0f} MB")
                        else:
                            _chip(f"{len(posts)} posts")
                            _chip(f"{len(all_v)} videos")
                            _chip(f"{total:.0f} MB")
                            if loaded_q:
                                _chip(f'"{loaded_q}"', "blue")
                        ui.space()
                        if scan_mode == "forum_topics":
                            unloaded_topics = [t for t in forum_topics if not t.get("loaded")]
                            if unloaded_topics:
                                ui.button(
                                    f"Fetch All  ({len(unloaded_topics)})", icon="cloud_download",
                                    on_click=lambda: _fetch_all_topics(),
                                ).props("flat dense").style(
                                    f"font-size:.75rem; color:{_BLUE};"
                                ).tooltip("Fetch all unloaded topics at once")
                        ui.button(
                            "Select all", icon="done_all",
                            on_click=lambda p=posts: _sel_all(p),
                        ).props("flat dense").style(f"font-size:.75rem; color:{_MUTED};")
                        ui.button(
                            "Clear", icon="remove_done",
                            on_click=lambda p=posts: _sel_clear(p),
                        ).props("flat dense").style(f"font-size:.75rem; color:{_MUTED};")
                        dl_label = (
                            f"Download  ({len(sel)})  ·  {sel_mb:.0f} MB" if sel
                            else "Download selected"
                        )
                        ui.button(
                            dl_label,
                            icon="download",
                            on_click=lambda s=sel: _launch(s),
                        ).props("unelevated dense" + (" disabled" if not sel else "")).style(
                            f"background:{_BLUE if sel else _SURFACE}; color:{'#fff' if sel else _DIM};"
                            f" font-weight:600; font-size:.78rem;"
                        )

                # Topic / post tree
                _render_tree(posts, forum_topics, scan_mode)

                # Load more
                if state.get("scan_has_more"):
                    with ui.row().classes("w-full justify-center q-mt-sm"):
                        lbl = "Load more topics" if scan_mode == "forum_topics" else "Load more posts"
                        ui.button(
                            lbl, icon="expand_more",
                            on_click=lambda: _load_more(),
                        ).props("flat" + (" loading disabled" if loading_more else "")).style(
                            f"color:{_MUTED}; font-size:.78rem;"
                        )

        def _chip(text: str, kind: str = "grey") -> None:
            ui.element("span").classes(f"chip chip-{kind}").text = text

        def _trf(tid) -> set:
            return state.get("topic_res_filters", {}).get(tid, set())

        def _toggle_trf(tid, res: str) -> None:
            filters = state.setdefault("topic_res_filters", {})
            f = filters.setdefault(tid, set())
            if res in f:
                f.discard(res)
            else:
                f.add(res)
            _render_inv()

        def _clear_trf(tid) -> None:
            state.setdefault("topic_res_filters", {}).pop(tid, None)
            _render_inv()

        def _thf(tid) -> set:
            return state.get("topic_hashtag_filters", {}).get(tid, set())

        def _toggle_thf(tid, tag: str) -> None:
            filters = state.setdefault("topic_hashtag_filters", {})
            f = filters.setdefault(tid, set())
            if tag in f:
                f.discard(tag)
            else:
                f.add(tag)
            _render_inv()

        def _clear_thf(tid) -> None:
            state.setdefault("topic_hashtag_filters", {}).pop(tid, None)
            _render_inv()

        def _clear_all_filters(tid) -> None:
            state.setdefault("topic_res_filters", {}).pop(tid, None)
            state.setdefault("topic_hashtag_filters", {}).pop(tid, None)
            _render_inv()

        def _sel_all(posts: dict) -> None:
            for gid, p in posts.items():
                if not p.get("downloaded"):
                    post_selections[gid] = True
            _render_inv()

        def _sel_clear(posts: dict) -> None:
            post_selections.clear()
            _render_inv()

        def _render_tree(posts: dict, forum_topics: list, scan_mode: str) -> None:
            def _filtered_targets(topic_posts: list, tid) -> list:
                trf = _trf(tid)
                thf = _thf(tid)
                result = []
                for _, p in topic_posts:
                    if p.get("downloaded"):
                        continue
                    if thf:
                        post_tags = set(_extract_hashtags(p.get("description", "")))
                        if not post_tags & thf:
                            continue
                    for v in p["videos"]:
                        if not v.get("downloaded") and (not trf or v.get("resolution") in trf):
                            result.append(v)
                return result

            def _topic_filter_row(tid, tvids: list, topic_posts: list) -> None:
                trf = _trf(tid)
                thf = _thf(tid)
                has_active = bool(trf or thf)

                topic_res = sorted({v.get("resolution", "") for v in tvids if v.get("resolution")})
                topic_hashtags = sorted({
                    tag for _, p in topic_posts
                    for tag in _extract_hashtags(p.get("description", ""))
                })

                if not topic_res and not topic_hashtags:
                    return

                active_bg = (
                    "background:rgba(56,139,253,.12); border:1px solid rgba(56,139,253,.45);"
                    if has_active else f"border:1px solid {_BORDER};"
                )
                with ui.column().classes("gap-1 q-pb-sm").style(
                    f"padding:6px 10px; border-radius:6px; margin-bottom:6px; {active_bg}"
                ):
                    if has_active:
                        with ui.row().classes("items-center gap-1 q-mb-xs"):
                            ui.icon("filter_alt", size="13px").style(f"color:{_BLUE}")
                            ui.label("Filters active").style(
                                f"font-size:.68rem; font-weight:700; color:{_BLUE};"
                            )
                            ui.space()
                            ui.button(
                                "Clear all", icon="close",
                                on_click=lambda t=tid: _clear_all_filters(t),
                            ).props("flat dense").style(f"font-size:.68rem; color:{_RED};")

                    if topic_res:
                        with ui.row().classes("items-center gap-1 flex-wrap"):
                            ui.label("Res:").style(f"font-size:.7rem; color:{_DIM}; white-space:nowrap;")
                            for r in topic_res:
                                active = r in trf
                                label = f"✓ {r}" if active else r
                                ui.button(
                                    label, on_click=lambda res=r, t=tid: _toggle_trf(t, res),
                                ).props("unelevated dense").style(
                                    "font-size:.72rem; min-height:24px; padding:0 10px;"
                                    + (
                                        f" background:#1d6feb; color:#fff; font-weight:700;"
                                        f" border:2px solid #5ba3ff;"
                                        f" box-shadow:0 0 8px rgba(91,163,255,.55);"
                                        if active else
                                        f" background:{_SURFACE}; color:{_DIM};"
                                        f" border:1px solid {_BORDER};"
                                    )
                                )
                            if trf and not thf:
                                ui.button(
                                    "✕", on_click=lambda t=tid: _clear_trf(t),
                                ).props("flat dense").style(f"font-size:.7rem; color:{_RED};")

                    if topic_hashtags:
                        with ui.row().classes("items-center gap-1 flex-wrap"):
                            ui.label("Tags:").style(f"font-size:.7rem; color:{_DIM}; white-space:nowrap;")
                            for tag in topic_hashtags:
                                active = tag in thf
                                label = f"✓ {tag}" if active else tag
                                ui.button(
                                    label, on_click=lambda tg=tag, t=tid: _toggle_thf(t, tg),
                                ).props("unelevated dense").style(
                                    "font-size:.72rem; min-height:24px; padding:0 10px;"
                                    + (
                                        f" background:#7c3aed; color:#fff; font-weight:700;"
                                        f" border:2px solid #c084fc;"
                                        f" box-shadow:0 0 8px rgba(192,132,252,.55);"
                                        if active else
                                        f" background:{_SURFACE}; color:{_DIM};"
                                        f" border:1px solid {_BORDER};"
                                    )
                                )
                            if thf and not trf:
                                ui.button(
                                    "✕", on_click=lambda t=tid: _clear_thf(t),
                                ).props("flat dense").style(f"font-size:.7rem; color:{_RED};")

            if scan_mode == "forum_topics" and forum_topics:
                topic_map = {tid: tp for tid, tp in _group_by_topic(posts)}
                for topic in forum_topics:
                    tid      = topic["topic_id"]
                    tlabel   = topic.get("topic_name") or f"Topic #{tid}"
                    tp       = topic_map.get(tid, [])
                    loaded   = topic.get("loaded", False)
                    tvids    = [v for _, p in tp for v in p["videos"]]
                    tmb      = sum(v["size_mb"] for v in tvids)
                    tgts     = _filtered_targets(tp, tid)

                    hdr = tlabel
                    if loaded:
                        res_counts: dict = {}
                        for v in tvids:
                            r = v.get("resolution", "")
                            if r:
                                res_counts[r] = res_counts.get(r, 0) + 1
                        res_part = ("  ·  " + "  ".join(
                            f"{c}×{r}" for r, c in sorted(res_counts.items())
                        )) if res_counts else ""
                        hdr += f"  ·  {len(tp)} posts  ·  {len(tvids)} vids  ·  {tmb:.0f} MB{res_part}"
                        if _trf(tid) or _thf(tid):
                            hdr += "  ·  🔍"
                    else:
                        hdr += f"  ·  updated {topic.get('last_update', '--')}"

                    if not loaded:
                        fetching = tid in state.get("fetching_topics", set())
                        with ui.row().classes("w-full items-center gap-2").style(
                            f"padding:8px 12px; background:{_SURFACE}; border:1px solid {_BORDER};"
                            " border-radius:8px; margin-bottom:2px;"
                        ):
                            ui.icon("topic", size="15px").style(f"color:{_DIM}")
                            ui.label(tlabel).style(
                                f"font-size:.82rem; font-weight:500; color:{_TEXT}; flex:1;"
                                " overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                            )
                            ui.label(f"updated {topic.get('last_update', '--')}").style(
                                f"font-size:.7rem; color:{_DIM}; white-space:nowrap;"
                            )
                            ui.button(
                                "Fetching…" if fetching else "Fetch topic",
                                icon="cloud_download",
                                on_click=lambda t=topic: _load_topic(t),
                            ).props(
                                "unelevated dense" + (" loading disabled" if fetching else "")
                            ).style(
                                f"background:{_SURFACE}; color:{_BLUE if not fetching else _MUTED};"
                                f" border:1px solid {_BORDER}; font-size:.75rem;"
                            )
                    else:
                        is_open = tid in expanded_topics
                        with ui.expansion(hdr, value=is_open).classes("w-full") as exp:
                            exp.on("update:model-value", lambda e, t=tid: (
                                expanded_topics.add(t) if e.args else expanded_topics.discard(t)
                            ))
                            _topic_filter_row(tid, tvids, tp)
                            _topic_actions(tp, tgts, tid)
                            trf = _trf(tid)
                            thf = _thf(tid)
                            for gid, post in tp:
                                _render_post(gid, post, trf, thf)
            else:
                for tid, tp in _group_by_topic(posts):
                    lbl    = _topic_label_of(tp[0][1]) if tid else "General"
                    tvids  = [v for _, p in tp for v in p["videos"]]
                    tmb    = sum(v["size_mb"] for v in tvids)
                    tgts   = _filtered_targets(tp, tid)
                    filter_indicator = "  ·  🔍" if (_trf(tid) or _thf(tid)) else ""
                    hdr    = f"{lbl}  ·  {len(tp)} posts  ·  {len(tvids)} videos  ·  {tmb:.1f} MB{filter_indicator}"
                    is_open = tid in expanded_topics
                    with ui.expansion(hdr, value=is_open).classes("w-full") as exp:
                        exp.on("update:model-value", lambda e, t=tid: (
                            expanded_topics.add(t) if e.args else expanded_topics.discard(t)
                        ))
                        _topic_filter_row(tid, tvids, tp)
                        _topic_actions(tp, tgts, tid)
                        trf = _trf(tid)
                        thf = _thf(tid)
                        for gid, post in tp:
                            _render_post(gid, post, trf, thf)

        def _topic_actions(topic_posts: list, tgts: list, tid) -> None:
            with ui.row().classes("items-center gap-1 q-pb-xs").style(
                f"border-bottom:1px solid {_BORDER}; margin-bottom:6px;"
            ):
                ui.space()
                btn_sel = ui.button(
                    "Select topic", icon="check_box",
                    on_click=lambda tp=topic_posts, t=tid: _sel_topic(tp, t),
                ).props("flat dense").style(f"font-size:.72rem; color:{_MUTED};")
                if not tgts:
                    btn_sel.props("disabled")
                btn_dl = ui.button(
                    f"Download  ({len(tgts)})", icon="download",
                    on_click=lambda t=tgts: _launch(t),
                ).props("flat dense").style(
                    f"font-size:.72rem; color:{_BLUE if tgts else _DIM};"
                )
                if not tgts:
                    btn_dl.props("disabled")

        def _sel_topic(topic_posts: list, tid) -> None:
            trf = _trf(tid)
            thf = _thf(tid)
            for gid, p in topic_posts:
                if not p.get("downloaded"):
                    if thf:
                        post_tags = set(_extract_hashtags(p.get("description", "")))
                        if not post_tags & thf:
                            continue
                    vids = p["videos"]
                    if not trf or any(v.get("resolution") in trf for v in vids if not v.get("downloaded")):
                        post_selections[gid] = True
            _render_inv()

        def _render_post(gid: str, post: dict, res_filter: set, hashtag_filter: set = None) -> None:
            if hashtag_filter:
                post_tags = set(_extract_hashtags(post.get("description", "")))
                if not post_tags & hashtag_filter:
                    return
            post_dl      = post.get("downloaded", False)
            all_videos   = post["videos"]
            videos       = [v for v in all_videos if not res_filter or v.get("resolution") in res_filter]
            if not videos:
                return
            n   = len(videos)
            tmb = sum(v["size_mb"] for v in videos)
            title = _get_post_title(post)

            with ui.element("div").classes("post-row w-full"):
                # Post header
                with ui.row().classes("w-full items-center gap-2"):
                    chk = ui.checkbox(
                        "", value=post_selections.get(gid, False),
                        on_change=lambda e, g=gid: post_selections.__setitem__(g, e.value),
                    ).props("dense dark")
                    if post_dl:
                        chk.props("disabled")

                    if post_dl:
                        ui.icon("check_circle", size="15px").style(f"color:{_GREEN}")
                    else:
                        ui.icon("folder_open", size="15px").style(f"color:{_MUTED}")

                    ui.label(title).style(
                        f"font-size:.82rem; font-weight:500; color:{'#3fb950' if post_dl else _TEXT};"
                        " flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                    )

                    ui.label(post["date"]).style(f"font-size:.68rem; color:{_DIM}; white-space:nowrap;")
                    ui.element("span").classes("chip chip-grey").style(
                        "font-size:.62rem;"
                    ).text = f"{n} video{'s' if n > 1 else ''}  ·  {tmb:.1f} MB"

                    post_btn = ui.button(
                        icon="check" if post_dl else "download",
                        on_click=lambda v=videos: _launch(v),
                    ).props("flat round dense").style(
                        f"color:{_GREEN if post_dl else _BLUE};"
                    ).tooltip("Download all in post")
                    if post_dl:
                        post_btn.props("disabled")

                # Description
                if not post_dl and post.get("description"):
                    ui.label(post["description"][:180]).style(
                        f"font-size:.72rem; color:{_DIM}; padding:2px 0 4px 26px;"
                    )

                # Videos
                with ui.column().classes("w-full gap-0"):
                    for vv in videos:
                        vdl = vv.get("downloaded", False)
                        res = vv.get("resolution", "")
                        _row_cls = (
                            "w-full items-center gap-2 video-row"
                            if vdl else
                            "w-full items-center gap-2 video-row-thumb"
                        )
                        with ui.row().classes(_row_cls):
                            if vdl:
                                ui.icon("check_circle", size="13px").style(
                                    f"color:{_GREEN}; flex-shrink:0;"
                                )
                            elif state.get("scan_loaded_group"):
                                _chat_q = urllib.parse.quote(state.get("scan_loaded_group", ""))
                                _sess_q = urllib.parse.quote(_sess())
                                (
                                    ui.image(
                                        f"/api/thumbnail?msg_id={vv['id']}"
                                        f"&chat={_chat_q}&session={_sess_q}"
                                    )
                                    .props("no-spinner fit=cover")
                                    .style(
                                        "width:88px; min-width:88px; height:55px;"
                                        " border-radius:4px; flex-shrink:0;"
                                        f" background:{_CARD};"
                                    )
                                )
                            else:
                                ui.icon("play_circle_outline", size="13px").style(
                                    f"color:{_DIM}; flex-shrink:0;"
                                )
                            ui.label(vv["filename"]).style(
                                f"font-family:monospace; font-size:.75rem;"
                                f" color:{_MUTED if vdl else _TEXT}; flex:1;"
                                "overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                            )
                            if res:
                                ui.label(res).classes(
                                    "chip chip-blue" if res in res_filter else "chip chip-grey"
                                ).style("font-size:.6rem; flex-shrink:0;")
                            ui.label(f"{vv['size_mb']} MB").style(
                                f"font-size:.68rem; color:{_DIM}; white-space:nowrap; flex-shrink:0;"
                            )
                            vbtn = ui.button(
                                icon="check" if vdl else "download",
                                on_click=lambda v=vv: _launch([v]),
                            ).props("flat round dense").style(
                                f"color:{_GREEN if vdl else _BLUE}; width:28px; flex-shrink:0;"
                            ).tooltip("Downloaded" if vdl else f"Download {vv['filename']}")
                            if vdl:
                                vbtn.props("disabled")

        async def _load_topic(topic: dict) -> None:
            tid    = topic["topic_id"]
            tlabel = topic.get("topic_name") or f"Topic #{tid}"
            state.setdefault("fetching_topics", set()).add(tid)
            _render_inv()
            status = None
            payload = None
            try:
                status, payload, _ = await run.io_bound(
                    asyncio.run,
                    fetch_topic_videos(
                        _aid(), _ahash(), _sess(),
                        state.get("scan_loaded_group", ""),
                        tid, topic_name=topic.get("topic_name", ""),
                    ),
                )
                if status == "SUCCESS":
                    state["found_videos"] = _merge_videos(state["found_videos"], payload)
                    _mark_loaded(state, tid)
                    ui.notify(f'Loaded {len(payload)} video(s) from "{tlabel}".', type="positive", position="top-right")
                elif status == "AUTH_NEEDED":
                    ui.notify("Run `python login.py` to authenticate.", type="warning", position="top-right")
                else:
                    ui.notify(f"Load failed: {payload}", type="negative", position="top-right")
            except Exception as exc:
                ui.notify(f"Load failed: {exc}", type="negative", position="top-right")
            finally:
                state.get("fetching_topics", set()).discard(tid)
                _render_inv()
                await asyncio.sleep(0)

        async def _fetch_all_topics() -> None:
            unloaded = [
                t for t in state.get("forum_topics", [])
                if not t.get("loaded") and t["topic_id"] not in state.get("fetching_topics", set())
            ]
            if not unloaded:
                ui.notify("All topics already loaded.", type="info", position="top-right")
                return
            ui.notify(f"Fetching {len(unloaded)} topics…", type="info", position="top-right")
            for topic in unloaded:
                await _load_topic(topic)

        def _clear_scan() -> None:
            target_input.set_value("")
            search_input.set_value("")
            reset_scan_state(state)
            state["topic_res_filters"] = {}
            state["topic_hashtag_filters"] = {}
            post_selections.clear()
            expanded_topics.clear()
            _render_inv()

        async def _load_more() -> None:
            state["loading_more"] = True
            _render_inv()
            scan_mode = state.get("scan_mode", "messages")
            aid, ah, sess = _aid(), _ahash(), _sess()
            target   = state.get("scan_loaded_group", "")
            search_q = state.get("scan_loaded_search_query", "")
            page_sz  = int(batch_input.value or 50)

            if scan_mode == "forum_topics":
                cur = state.get("scan_forum_cursor", {})
                status, payload, next_cur, _ = await run.io_bound(
                    asyncio.run,
                    fetch_forum_topics(
                        aid, ah, sess, target, page_size=page_sz,
                        offset_date=cur.get("offset_date", ""),
                        offset_id=cur.get("offset_id", 0),
                        offset_topic=cur.get("offset_topic", 0),
                    ),
                )
                if status == "SUCCESS":
                    state["forum_topics"] = _merge_topics(state["forum_topics"], payload)
                    state["scan_forum_cursor"] = next_cur
                    state["scan_has_more"] = _has_cursor(next_cur)
                elif status == "AUTH_NEEDED":
                    state["loading_more"] = False
                    ui.notify("Authentication required.", type="warning", position="top-right"); return
                else:
                    state["loading_more"] = False
                    ui.notify(f"Load more failed: {payload}", type="negative", position="top-right"); return
            else:
                status, payload, nxt, _ = await run.io_bound(
                    asyncio.run,
                    fetch_group_videos(
                        aid, ah, sess, target, page_size=page_sz,
                        offset_id=state.get("scan_offset_id", 0),
                        search_query=search_q, expand_topics=False,
                    ),
                )
                if status == "SUCCESS":
                    state["found_videos"] = _merge_videos(state["found_videos"], payload)
                    state["scan_offset_id"] = nxt
                    state["scan_has_more"] = nxt != 0
                elif status == "AUTH_NEEDED":
                    ui.notify("Authentication required.", type="warning", position="top-right"); return
                else:
                    ui.notify(f"Load more failed: {payload}", type="negative", position="top-right"); return
            state["loading_more"] = False
            _render_inv()

        async def _do_scan(fetch_all: bool) -> None:
            aid, ah, sess = _aid(), _ahash(), _sess()
            target   = (target_input.value or "").strip()
            search_q = (search_input.value or "").strip()
            page_sz  = int(batch_input.value or 50)

            if not aid or not ah or not target:
                ui.notify("Enter API credentials and a target group.", type="negative", position="top-right")
                return

            reset_scan_state(state, target_group=target, search_query=search_q)
            state["topic_res_filters"] = {}
            state["topic_hashtag_filters"] = {}
            post_selections.clear()
            expanded_topics.clear()
            scan_btn.props("loading")
            scan_all_btn.props("loading")

            try:
                status = "NOT_STARTED"
                payload = None

                if not search_q:
                    collected: list = []
                    cursor = state["scan_forum_cursor"]
                    while True:
                        status, payload, next_cur, _ = await run.io_bound(
                            asyncio.run,
                            fetch_forum_topics(
                                aid, ah, sess, target, page_size=page_sz,
                                offset_date=cursor.get("offset_date", ""),
                                offset_id=cursor.get("offset_id", 0),
                                offset_topic=cursor.get("offset_topic", 0),
                            ),
                        )
                        if status != "SUCCESS":
                            break
                        collected = _merge_topics(collected, payload)
                        cursor = next_cur
                        if not fetch_all or not _has_cursor(next_cur):
                            break

                    if status == "SUCCESS" and collected:
                        state["forum_topics"] = collected
                        state["scan_forum_cursor"] = cursor
                        state["scan_has_more"] = not fetch_all and _has_cursor(cursor)
                        state["scan_mode"] = "forum_topics"
                        add_recent_group(target)
                        _render_recent_groups()
                        ui.notify(f"Loaded {len(collected)} topics.", type="positive", position="top-right")
                        _render_inv(); return
                    elif status == "AUTH_NEEDED":
                        ui.notify("Run `python login.py` to authenticate.", type="warning", position="top-right"); return
                    elif status not in {"NOT_FORUM", "SUCCESS"}:
                        ui.notify(f"Scan error: {payload}", type="negative", position="top-right"); return

                # message scan
                collected_v: list = []
                offset = 0
                while True:
                    status, payload, nxt, _ = await run.io_bound(
                        asyncio.run,
                        fetch_group_videos(
                            aid, ah, sess, target, page_size=page_sz,
                            offset_id=offset, search_query=search_q, expand_topics=False,
                        ),
                    )
                    if status != "SUCCESS":
                        break
                    collected_v = _merge_videos(collected_v, payload)
                    offset = nxt
                    if not fetch_all or nxt == 0:
                        break

                if status == "SUCCESS":
                    state["found_videos"] = collected_v
                    state["scan_offset_id"] = offset
                    state["scan_has_more"] = not fetch_all and offset != 0
                    add_recent_group(target)
                    _render_recent_groups()
                    ui.notify(f"Found {len(collected_v)} video(s).", type="positive", position="top-right")
                elif status == "AUTH_NEEDED":
                    ui.notify("Run `python login.py` to authenticate.", type="warning", position="top-right")
                else:
                    ui.notify(f"Scan error: {payload}", type="negative", position="top-right")

            finally:
                scan_btn.props(remove="loading")
                scan_all_btn.props(remove="loading")
                _render_inv()

        _render_inv()


ui.run(
    title="VibeTGVideoDownloader",
    favicon="🎬",
    port=8080,
    dark=True,
    reload=True,
)
