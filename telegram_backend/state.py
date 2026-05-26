import json
import os
import threading
from datetime import datetime

from .filesystem import (
    _SAFE_ROOT,
    append_download_log,
    build_download_relative_path,
    get_expected_size_bytes,
    is_video_downloaded,
)

RECENT_GROUPS_FILE = "recent_groups.json"
MAX_RECENT_GROUPS = 20

TASK_STATUS_ICONS = {
    "queued": "🕓",
    "downloading": "⬇️",
    "done": "✅",
    "skipped": "⏭️",
    "stopped": "⛔",
    "error": "❌",
}

_ACTIVE_TASK_STATUSES = {"queued", "downloading"}
_DONE_TASK_STATUSES = {"done", "skipped"}


# ── Recent groups ────────────────────────────────────────────────────────────

def load_recent_groups() -> list:
    path = os.path.join(_SAFE_ROOT, RECENT_GROUPS_FILE)
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return []


def save_recent_groups(groups: list) -> None:
    path = os.path.join(_SAFE_ROOT, RECENT_GROUPS_FILE)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(groups, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add_recent_group(handle: str, display_name: str = "") -> list:
    handle = handle.strip()
    if not handle:
        return load_recent_groups()
    groups = load_recent_groups()
    groups = [g for g in groups if g.get("handle", "").lower() != handle.lower()]
    groups.insert(0, {
        "handle": handle,
        "display_name": display_name.strip() or handle,
        "last_used": datetime.now().isoformat(timespec="seconds"),
    })
    groups = groups[:MAX_RECENT_GROUPS]
    save_recent_groups(groups)
    return groups


def remove_recent_group(handle: str) -> list:
    groups = load_recent_groups()
    groups = [g for g in groups if g.get("handle", "").lower() != handle.lower()]
    save_recent_groups(groups)
    return groups


# ── App state ────────────────────────────────────────────────────────────────

def default_download_progress() -> dict:
    return {
        "active": False,
        "file_idx": 0,
        "total_files": 0,
        "filename": "",
        "bytes_done": 0,
        "bytes_total": 1,
        "chunks_done": 0,
        "chunks_total": 1,
        "files_done": 0,
        "error": None,
        "speed_mbps": 0.0,
        "task_queue": [],
        "per_file_progress": {},   # task_id str → {bytes_done, bytes_total, speed_mbps, filename}
    }


def default_scan_cursor() -> dict:
    return {"offset_date": "", "offset_id": 0, "offset_topic": 0}


def create_app_state(stop_event_factory=threading.Event) -> dict:
    state = {}
    ensure_session_defaults(state, stop_event_factory=stop_event_factory)
    return state


def ensure_session_defaults(session_state, stop_event_factory=threading.Event) -> None:
    session_state.setdefault("found_videos", [])
    session_state.setdefault("forum_topics", [])
    session_state.setdefault("download_progress", default_download_progress())
    session_state.setdefault("stop_event", stop_event_factory())
    session_state.setdefault("scan_offset_id", 0)
    session_state.setdefault("scan_forum_cursor", default_scan_cursor())
    session_state.setdefault("scan_has_more", False)
    session_state.setdefault("scan_mode", "messages")
    session_state.setdefault("scan_loaded_group", "")
    session_state.setdefault("scan_loaded_search_query", "")


def reset_scan_state(session_state, target_group: str = "", search_query: str = "") -> None:
    session_state["found_videos"] = []
    session_state["forum_topics"] = []
    session_state["scan_offset_id"] = 0
    session_state["scan_forum_cursor"] = default_scan_cursor()
    session_state["scan_has_more"] = False
    session_state["scan_mode"] = "messages"
    session_state["scan_loaded_group"] = target_group
    session_state["scan_loaded_search_query"] = (search_query or "").strip()


# ── Queue management ─────────────────────────────────────────────────────────

def sync_progress_summary(progress_state) -> None:
    if progress_state is None:
        return
    tasks = progress_state.get("task_queue", [])
    progress_state["total_files"] = len(tasks)
    progress_state["files_done"] = sum(
        1 for task in tasks if task.get("status") in _DONE_TASK_STATUSES
    )


def queue_downloads(progress_state, video_meta_list: list, default_chat: str = "", downloaded_files=None) -> dict:
    if progress_state is None:
        return {"added": 0, "already_downloaded": 0}
    task_queue = progress_state.setdefault("task_queue", [])
    added = 0
    already_downloaded = 0
    downloaded_files = downloaded_files or set()
    for meta in video_meta_list:
        if is_video_downloaded(meta, downloaded_files):
            already_downloaded += 1
            continue
        chat_name = meta.get("chat") or default_chat
        existing = next(
            (
                task for task in task_queue
                if task.get("id") == meta["id"] and task.get("chat", "") == chat_name
            ),
            None,
        )
        if existing is None:
            task_queue.append({
                "id": meta["id"],
                "filename": meta["filename"],
                "size_bytes": get_expected_size_bytes(meta),
                "size_mb": meta.get("size_mb", 0),
                "date": meta.get("date", ""),
                "chat": chat_name,
                "topic_id": meta.get("topic_id", 0),
                "topic_name": meta.get("topic_name", ""),
                "download_rel_path": build_download_relative_path({**meta, "chat": chat_name}),
                "status": "queued",
                "error": "",
            })
            append_download_log(
                "INFO",
                f"Queued msg_id={meta['id']} chat={chat_name} target={build_download_relative_path({**meta, 'chat': chat_name})}",
            )
            added += 1
            continue
        if existing.get("status") in {"error", "stopped"}:
            existing.update({
                "filename": meta["filename"],
                "size_bytes": get_expected_size_bytes(meta),
                "size_mb": meta.get("size_mb", 0),
                "date": meta.get("date", ""),
                "chat": chat_name,
                "topic_id": meta.get("topic_id", 0),
                "topic_name": meta.get("topic_name", ""),
                "download_rel_path": build_download_relative_path({**meta, "chat": chat_name}),
                "status": "queued",
                "error": "",
            })
            append_download_log(
                "INFO",
                f"Re-queued msg_id={meta['id']} chat={chat_name} target={build_download_relative_path({**meta, 'chat': chat_name})}",
            )
            added += 1
    sync_progress_summary(progress_state)
    return {"added": added, "already_downloaded": already_downloaded}


def remove_from_queue(progress_state, task_id: int) -> bool:
    """Remove a queued (not yet downloading) task by ID. Returns True if removed."""
    if progress_state is None:
        return False
    queue = progress_state.get("task_queue", [])
    for i, task in enumerate(queue):
        if task.get("id") == task_id and task.get("status") == "queued":
            queue.pop(i)
            sync_progress_summary(progress_state)
            return True
    return False


def filter_completed_incomplete(incomplete: list, downloaded_files: set) -> list:
    return [
        item for item in incomplete
        if not is_video_downloaded(item, downloaded_files)
    ]


def filter_visible_incomplete(progress_state, incomplete: list) -> list:
    if progress_state is None:
        return incomplete
    active_keys = {
        (task.get("id"), task.get("chat", ""))
        for task in progress_state.get("task_queue", [])
        if task.get("status") in _ACTIVE_TASK_STATUSES
    }
    return [
        item for item in incomplete
        if (item.get("msg_id"), item.get("chat", "")) not in active_keys
    ]


def set_task_status(progress_state, video_id: int, status: str, error: str = "") -> None:
    if progress_state is None:
        return
    for task in progress_state.get("task_queue", []):
        if task.get("id") == video_id:
            task["status"] = status
            task["error"] = error
            sync_progress_summary(progress_state)
            return


def mark_unfinished_tasks(progress_state, status: str) -> None:
    if progress_state is None:
        return
    for task in progress_state.get("task_queue", []):
        if task.get("status") in _ACTIVE_TASK_STATUSES:
            task["status"] = status
    sync_progress_summary(progress_state)


# ── Post / video aggregation ─────────────────────────────────────────────────

def build_posts(video_items: list) -> dict:
    posts = {}
    for video in video_items:
        topic_id = video.get("topic_id", 0)
        post_id = video.get("grouped_id") or video["id"]
        group_id = f"{topic_id}:{post_id}"
        post = posts.setdefault(
            group_id,
            {
                "group_id": group_id,
                "date": video["date"],
                "description": "",
                "topic_id": topic_id,
                "topic_name": video.get("topic_name", ""),
                "videos": [],
            },
        )
        post["videos"].append(video)
        if video.get("description") and not post["description"]:
            post["description"] = video["description"]
        if video.get("topic_name") and not post.get("topic_name"):
            post["topic_name"] = video["topic_name"]

    for post in posts.values():
        post["videos"].sort(key=lambda item: item["filename"].lower())

    return posts


def apply_downloaded_flags(posts: dict, downloaded_files: set) -> None:
    for post in posts.values():
        for video in post["videos"]:
            video["downloaded"] = is_video_downloaded(video, downloaded_files)
        post["downloaded"] = all(video.get("downloaded", False) for video in post["videos"])
