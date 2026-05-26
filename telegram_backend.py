import asyncio
import json
import os
import re
import shutil
import threading
import time
from collections import deque
from datetime import datetime

from telethon import TelegramClient, functions
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

DEFAULT_DOWNLOADS_DIR = "downloads"
DEFAULT_VAULT_DIR = DEFAULT_DOWNLOADS_DIR
DOWNLOAD_LOG_FILE = "telegram_downloads.log"
RECENT_GROUPS_FILE = "recent_groups.json"
MAX_RECENT_GROUPS = 20
PAGE_SIZE = 100
_REQUEST_SIZE = 1024 * 1024  # 1 MB per chunk (Telethon maximum)
_ACTIVE_TASK_STATUSES = {"queued", "downloading"}
_DONE_TASK_STATUSES = {"done", "skipped"}
_RESOLUTION_HINT_RE = re.compile(r"(?<!\d)(2160|1440|1080|720|576|540|480|360|240)p(?!\d)", re.IGNORECASE)
_STANDARD_HEIGHTS = frozenset({2160, 1440, 1080, 720, 576, 540, 480, 360, 240})
TASK_STATUS_ICONS = {
    "queued": "🕓",
    "downloading": "⬇️",
    "done": "✅",
    "skipped": "⏭️",
    "stopped": "⛔",
    "error": "❌",
}
_SAFE_ROOT = os.path.realpath(os.getcwd())


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


def _session_file_path(session_name: str) -> str:
    return session_name if session_name.endswith(".session") else f"{session_name}.session"


async def resolve_group_title(api_id, api_hash, session, target_chat) -> str:
    """Return the display title of a Telegram chat/channel. Falls back to empty string."""
    client = create_telegram_client(session, api_id, api_hash, purpose="scan")
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return ""
        entity = await client.get_entity(target_chat)
        return (
            getattr(entity, "title", None)
            or getattr(entity, "first_name", None)
            or ""
        ).strip()
    except Exception:
        return ""
    finally:
        await client.disconnect()


def prepare_client_session(session_name: str, purpose: str = "") -> str:
    if not purpose:
        return session_name

    base_session = _session_file_path(session_name)
    derived_session_name = f"{session_name}__{purpose}"
    derived_session = _session_file_path(derived_session_name)

    if os.path.exists(base_session) and not os.path.exists(derived_session):
        shutil.copy2(base_session, derived_session)

    if os.path.exists(derived_session):
        return derived_session_name

    return session_name


def create_telegram_client(session_name: str, api_id: int, api_hash: str, purpose: str = "") -> TelegramClient:
    client_session = prepare_client_session(session_name, purpose)
    return TelegramClient(
        client_session,
        api_id,
        api_hash,
        flood_sleep_threshold=60,
        request_retries=5,
    )


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


def sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r"[^\w\-_\. ]", "_", name)
    return name.strip() or "video.mp4"


def sanitize_folder_name(name: str) -> str:
    sanitized = re.sub(r"[^\w\-\. ]", "_", (name or "").strip())
    sanitized = sanitized.strip(" .")
    return sanitized or "General"


def get_topic_folder_name(video_meta: dict) -> str:
    topic_name = (video_meta.get("topic_name") or "").strip()
    if topic_name:
        return sanitize_folder_name(topic_name)

    topic_id = video_meta.get("topic_id", 0)
    if topic_id:
        return sanitize_folder_name(f"Topic #{topic_id}")

    return ""


def build_download_relative_path(video_meta: dict) -> str:
    stored_rel_path = (video_meta.get("download_rel_path") or "").strip()
    if stored_rel_path:
        return stored_rel_path

    filename = sanitize_filename(video_meta["filename"])
    topic_folder = get_topic_folder_name(video_meta)
    if topic_folder:
        return os.path.join(topic_folder, filename)
    return filename


def build_target_file_path(downloads_dir: str, video_meta: dict) -> str:
    return os.path.join(downloads_dir, build_download_relative_path(video_meta))


def build_partial_file_path(downloads_dir: str, video_meta: dict) -> str:
    return build_target_file_path(downloads_dir, video_meta) + ".part"


def build_meta_file_path(downloads_dir: str, video_meta: dict) -> str:
    return build_target_file_path(downloads_dir, video_meta) + ".meta.json"


def get_expected_size_bytes(video_meta: dict) -> int:
    size_bytes = int(video_meta.get("size_bytes") or video_meta.get("total_size") or 0)
    if size_bytes > 0:
        return size_bytes
    return int(float(video_meta.get("size_mb", 0)) * 1048576)


def build_download_task(video_meta: dict, default_chat: str = "") -> dict:
    chat_name = video_meta.get("chat") or default_chat
    task_meta = dict(video_meta)
    if chat_name:
        task_meta["chat"] = chat_name

    return {
        "id": task_meta["id"],
        "filename": task_meta["filename"],
        "size_bytes": get_expected_size_bytes(task_meta),
        "size_mb": task_meta.get("size_mb", 0),
        "date": task_meta.get("date", ""),
        "chat": chat_name,
        "topic_id": task_meta.get("topic_id", 0),
        "topic_name": task_meta.get("topic_name", ""),
        "download_rel_path": build_download_relative_path(task_meta),
    }


def build_resume_task(incomplete_meta: dict) -> dict:
    return {
        "id": incomplete_meta["msg_id"],
        "filename": incomplete_meta["filename"],
        "size_bytes": incomplete_meta.get("size_bytes") or incomplete_meta.get("total_size", 0),
        "size_mb": incomplete_meta.get("size_mb", 0),
        "date": incomplete_meta.get("date", ""),
        "chat": incomplete_meta.get("chat", ""),
        "topic_id": incomplete_meta.get("topic_id", 0),
        "topic_name": incomplete_meta.get("topic_name", ""),
        "download_rel_path": incomplete_meta.get("download_rel_path", ""),
    }


def append_download_log(level: str, message: str) -> None:
    log_path = os.path.join(_SAFE_ROOT, DOWNLOAD_LOG_FILE)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {level}: {message}\n")
    except OSError:
        pass


def is_video_downloaded(video_meta: dict, downloaded_files: set) -> bool:
    rel_path = build_download_relative_path(video_meta)
    if rel_path in downloaded_files:
        return True

    filename = sanitize_filename(video_meta["filename"])
    return any(os.path.basename(path) == filename for path in downloaded_files)


def safe_abs_path(user_path: str) -> str:
    resolved = os.path.realpath(os.path.abspath(user_path))
    if not resolved.startswith(_SAFE_ROOT):
        raise ValueError(
            f"Download path '{user_path}' must be inside the project directory."
        )
    return resolved


def scan_incomplete_downloads(out_dir: str) -> list:
    incomplete = []
    try:
        abs_dir = safe_abs_path(out_dir)
        if not os.path.isdir(abs_dir):
            return []
        for root, _, files in os.walk(abs_dir):
            for fname in sorted(files):
                if not fname.endswith(".meta.json"):
                    continue
                meta_path = os.path.join(root, fname)
                try:
                    with open(meta_path) as meta_file:
                        meta = json.load(meta_file)
                    target_path = meta_path[: -len(".meta.json")]
                    part_path = target_path + ".part"
                    part_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
                    meta.setdefault("download_rel_path", os.path.relpath(target_path, abs_dir))
                    meta.setdefault("size_bytes", int(meta.get("total_size") or 0))
                    if meta.get("size_bytes") and not meta.get("size_mb"):
                        meta["size_mb"] = round(meta["size_bytes"] / 1048576, 2)
                    meta["_part_path"] = part_path
                    meta["_meta_path"] = meta_path
                    meta["_part_size_mb"] = round(part_size / 1048576, 2)
                    incomplete.append(meta)
                except Exception:
                    pass
    except Exception:
        pass
    return incomplete


_SKIP_FILENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def _is_system_file(fname: str) -> bool:
    return fname in _SKIP_FILENAMES or fname.startswith("._")


def scan_downloaded_files(out_dir: str) -> set:
    downloaded = set()
    try:
        abs_dir = safe_abs_path(out_dir)
        if not os.path.isdir(abs_dir):
            return downloaded
        for root, _, files in os.walk(abs_dir):
            for fname in files:
                full_path = os.path.join(root, fname)
                if not os.path.isfile(full_path):
                    continue
                if fname.endswith(".meta.json") or fname.endswith(".part") or _is_system_file(fname):
                    continue
                downloaded.add(os.path.relpath(full_path, abs_dir))
    except Exception:
        pass
    return downloaded


def list_downloaded_files(out_dir: str) -> list:
    """Return a sorted list of completed downloaded files with metadata."""
    result = []
    try:
        abs_dir = safe_abs_path(out_dir)
        if not os.path.isdir(abs_dir):
            return result
        for root, _, files in os.walk(abs_dir):
            for fname in sorted(files):
                if fname.endswith(".meta.json") or fname.endswith(".part") or _is_system_file(fname):
                    continue
                full_path = os.path.join(root, fname)
                if not os.path.isfile(full_path):
                    continue
                rel_path = os.path.relpath(full_path, abs_dir)
                folder = os.path.dirname(rel_path) or ""
                size_bytes = os.path.getsize(full_path)
                result.append({
                    "rel_path": rel_path,
                    "filename": fname,
                    "folder": folder,
                    "size_mb": round(size_bytes / 1048576, 2),
                    "abs_path": full_path,
                })
    except Exception:
        pass
    return sorted(result, key=lambda x: (x["folder"].lower(), x["filename"].lower()))


def delete_downloaded_file(abs_path: str) -> bool:
    """Safely delete a file that is inside the project root. Returns True on success."""
    try:
        resolved = os.path.realpath(abs_path)
        if not resolved.startswith(_SAFE_ROOT):
            return False
        if os.path.isfile(resolved):
            os.remove(resolved)
            return True
    except OSError:
        pass
    return False


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


def get_message_topic_id(msg) -> int:
    topic_id = getattr(msg, "reply_to_top_id", None)
    if topic_id:
        return topic_id

    reply_to = getattr(msg, "reply_to", None)
    if reply_to is None:
        return 0

    topic_id = getattr(reply_to, "reply_to_top_id", None)
    if topic_id:
        return topic_id

    if getattr(reply_to, "forum_topic", False):
        return getattr(reply_to, "reply_to_msg_id", None) or 0

    return 0


def get_topic_title_from_message(msg, topic_id: int) -> str:
    if msg is None:
        return f"Topic #{topic_id}"

    action = getattr(msg, "action", None)
    action_title = getattr(action, "title", None)
    if action_title:
        return action_title.strip()

    message_text = (getattr(msg, "message", "") or "").strip()
    if message_text:
        return message_text.splitlines()[0].strip()

    return f"Topic #{topic_id}"


def _detect_resolution_hint(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        match = _RESOLUTION_HINT_RE.search(text)
        if match:
            return f"{match.group(1)}p"
    return ""


def _extract_video_metadata(msg) -> tuple:
    media = getattr(msg, "video", None)
    filename = f"video_msg_{msg.id}.mp4"
    width = 0
    height = 0

    for attr in getattr(media, "attributes", []) or []:
        if isinstance(attr, DocumentAttributeFilename) and attr.file_name:
            filename = attr.file_name
        elif isinstance(attr, DocumentAttributeVideo):
            width = int(getattr(attr, "w", 0) or 0)
            height = int(getattr(attr, "h", 0) or 0)

    # Prefer actual encoded dimensions over filename/caption hints
    if width > 0 and height > 0:
        resolution = f"{height}p" if height in _STANDARD_HEIGHTS else f"{width}x{height}"
    else:
        resolution = _detect_resolution_hint(filename, getattr(msg, "message", "") or "")

    return filename, resolution


def _append_video_record(collected: dict, msg) -> bool:
    if not getattr(msg, "video", None) or msg.id in collected:
        return False

    collected[msg.id] = build_video_record(msg)
    return True


def _sort_video_records(video_items: list) -> list:
    return sorted(
        video_items,
        key=lambda item: (
            getattr(item.get("raw_msg"), "date", datetime.min),
            item.get("id", 0),
        ),
        reverse=True,
    )


def _parse_cursor_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _build_forum_topic_summary(topic) -> dict:
    topic_date = getattr(topic, "date", None)
    return {
        "topic_id": getattr(topic, "id", 0),
        "topic_name": (getattr(topic, "title", "") or "").strip() or f"Topic #{getattr(topic, 'id', 0)}",
        "top_message_id": getattr(topic, "top_message", 0),
        "last_update": topic_date.strftime("%Y-%m-%d %H:%M") if topic_date else "",
        "last_update_ts": topic_date.isoformat() if topic_date else "",
        "loaded": False,
    }


async def resolve_topic_names(client, chat_entity, topic_ids: set) -> dict:
    if not topic_ids:
        return {}

    topic_names = {}
    topic_messages = await client.get_messages(chat_entity, ids=list(topic_ids))
    if not isinstance(topic_messages, list):
        topic_messages = [topic_messages]

    for topic_id, topic_msg in zip(topic_ids, topic_messages):
        topic_names[topic_id] = get_topic_title_from_message(topic_msg, topic_id)

    return topic_names


async def fetch_forum_topics(
    api_id,
    api_hash,
    session,
    target_chat,
    page_size=PAGE_SIZE,
    offset_date="",
    offset_id=0,
    offset_topic=0,
):
    client = create_telegram_client(session, api_id, api_hash, purpose="scan")
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        return "AUTH_NEEDED", [], {"offset_date": "", "offset_id": 0, "offset_topic": 0}, 0

    try:
        chat_entity = await client.get_entity(target_chat)
        if not getattr(chat_entity, "forum", False):
            await client.disconnect()
            return "NOT_FORUM", [], {"offset_date": "", "offset_id": 0, "offset_topic": 0}, 0

        response = await client(functions.channels.GetForumTopicsRequest(
            channel=chat_entity,
            offset_date=_parse_cursor_date(offset_date),
            offset_id=offset_id or 0,
            offset_topic=offset_topic or 0,
            limit=page_size,
            q=None,
        ))

        topics = sorted(
            response.topics,
            key=lambda topic: (getattr(topic, "date", datetime.min) or datetime.min, getattr(topic, "id", 0)),
            reverse=True,
        )
        summaries = [_build_forum_topic_summary(topic) for topic in topics]

        next_cursor = {"offset_date": "", "offset_id": 0, "offset_topic": 0}
        if len(topics) >= page_size:
            last_topic = topics[-1]
            last_topic_date = getattr(last_topic, "date", None)
            next_cursor = {
                "offset_date": last_topic_date.isoformat() if last_topic_date else "",
                "offset_id": getattr(last_topic, "top_message", 0),
                "offset_topic": getattr(last_topic, "id", 0),
            }

        await client.disconnect()
        return "SUCCESS", summaries, next_cursor, len(summaries)
    except Exception as exc:
        await client.disconnect()
        return "ERROR", str(exc), {"offset_date": "", "offset_id": 0, "offset_topic": 0}, 0


async def fetch_topic_videos(
    api_id,
    api_hash,
    session,
    target_chat,
    topic_id,
    topic_name="",
):
    client = create_telegram_client(session, api_id, api_hash, purpose="scan")
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        return "AUTH_NEEDED", [], 0

    try:
        chat_entity = await client.get_entity(target_chat)
        collected_by_id = {}
        messages_scanned = 0

        async for msg in client.iter_messages(chat_entity, reply_to=topic_id):
            messages_scanned += 1
            _append_video_record(collected_by_id, msg)

        topic_label = topic_name
        if not topic_label:
            topic_names = await resolve_topic_names(client, chat_entity, {topic_id})
            topic_label = topic_names.get(topic_id, f"Topic #{topic_id}")

        collected = _sort_video_records(list(collected_by_id.values()))
        for item in collected:
            item["topic_id"] = topic_id
            item["topic_name"] = topic_label

        await client.disconnect()
        return "SUCCESS", collected, messages_scanned
    except Exception as exc:
        await client.disconnect()
        return "ERROR", str(exc), 0


def build_video_record(msg) -> dict:
    filename, resolution = _extract_video_metadata(msg)
    topic_id = get_message_topic_id(msg)

    return {
        "id": msg.id,
        "date": msg.date.strftime("%Y-%m-%d %H:%M"),
        "filename": filename,
        "size_bytes": msg.video.size,
        "size_mb": round(msg.video.size / (1024 * 1024), 2),
        "resolution": resolution,
        "description": (msg.message or "").strip(),
        "grouped_id": msg.grouped_id,
        "topic_id": topic_id,
        "topic_name": "",
        "raw_msg": msg,
    }


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


async def fetch_group_videos(
    api_id,
    api_hash,
    session,
    target_chat,
    page_size=PAGE_SIZE,
    offset_id=0,
    search_query="",
    expand_topics=False,
):
    client = create_telegram_client(session, api_id, api_hash, purpose="scan")
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        return "AUTH_NEEDED", [], 0, 0

    try:
        chat_entity = await client.get_entity(target_chat)
        collected_by_id = {}
        min_id_seen = 0
        messages_scanned = 0
        exhausted = True
        boundary_group_id = 0

        async for msg in client.iter_messages(
            chat_entity,
            offset_id=offset_id or 0,
            search=search_query or None,
        ):
            messages_scanned += 1
            if min_id_seen == 0 or msg.id < min_id_seen:
                min_id_seen = msg.id

            current_group_id = getattr(msg, "grouped_id", None) or 0

            if boundary_group_id:
                if current_group_id == boundary_group_id:
                    _append_video_record(collected_by_id, msg)
                    continue
                exhausted = False
                break

            added = _append_video_record(collected_by_id, msg)
            if not added:
                continue

            if len(collected_by_id) >= page_size:
                if current_group_id:
                    boundary_group_id = current_group_id
                    continue
                exhausted = False
                break

        if expand_topics and not search_query:
            topic_ids = {
                item.get("topic_id", 0)
                for item in collected_by_id.values()
                if item.get("topic_id", 0)
            }

            for topic_id in sorted(topic_ids):
                try:
                    async for topic_msg in client.iter_messages(chat_entity, reply_to=topic_id):
                        messages_scanned += 1
                        _append_video_record(collected_by_id, topic_msg)
                except Exception:
                    continue

        collected = _sort_video_records(list(collected_by_id.values()))
        topic_ids = {
            item.get("topic_id", 0)
            for item in collected
            if item.get("topic_id", 0)
        }
        topic_names = await resolve_topic_names(client, chat_entity, topic_ids)
        for item in collected:
            topic_id = item.get("topic_id", 0)
            if topic_id:
                item["topic_name"] = topic_names.get(topic_id, f"Topic #{topic_id}")

        next_offset = 0 if exhausted else min_id_seen

        await client.disconnect()
        return "SUCCESS", collected, next_offset, messages_scanned
    except Exception as exc:
        await client.disconnect()
        return "ERROR", str(exc), 0, 0


def _preallocate_file(path: str, size: int) -> None:
    with open(path, "wb") as file_handle:
        file_handle.seek(size - 1)
        file_handle.write(b"\x00")


def _write_at_offset(path: str, offset: int, data: bytes) -> None:
    with open(path, "r+b") as file_handle:
        file_handle.seek(offset)
        file_handle.write(data)


async def download_video_parallel(
    client, media, out_path: str, workers: int = 2,
    stop_event=None, on_progress=None,
) -> None:
    total = media.size
    total_chunks = -(-total // _REQUEST_SIZE)
    chunks_per_worker = -(-total_chunks // workers)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _preallocate_file, out_path, total)

    chunks_done = [0]
    bytes_done = [0]
    speed_samples: deque = deque(maxlen=30)

    async def fetch_segment(start_chunk: int):
        offset = start_chunk * _REQUEST_SIZE
        limit = min(chunks_per_worker, total_chunks - start_chunk)
        if limit <= 0:
            return
        async for data in client.iter_download(
            media,
            offset=offset,
            request_size=_REQUEST_SIZE,
            limit=limit,
        ):
            if stop_event and stop_event.is_set():
                raise asyncio.CancelledError("Stopped by user")
            write_offset = offset
            offset += len(data)
            await loop.run_in_executor(None, _write_at_offset, out_path, write_offset, data)
            chunks_done[0] += 1
            bytes_done[0] += len(data)
            now = time.monotonic()
            speed_samples.append((now, bytes_done[0]))
            if len(speed_samples) >= 2:
                dt = speed_samples[-1][0] - speed_samples[0][0]
                db = speed_samples[-1][1] - speed_samples[0][1]
                speed = db / max(dt, 0.1)
            else:
                speed = 0.0
            if on_progress:
                on_progress(chunks_done[0], total_chunks, bytes_done[0], total, speed)

    await asyncio.gather(*[
        fetch_segment(index * chunks_per_worker)
        for index in range(workers)
        if index * chunks_per_worker < total_chunks
    ])


async def download_selected_chunks(
    api_id, api_hash, session, target_chat, video_meta_list, vault_dir, downloads_dir,
    file_workers=2, stop_event=None, progress_state=None, slot=0,
):
    del vault_dir
    abs_downloads_dir = safe_abs_path(downloads_dir)

    client = create_telegram_client(session, api_id, api_hash, purpose=f"download_{slot}")
    await client.connect()

    os.makedirs(abs_downloads_dir, exist_ok=True)
    chat_entity = await client.get_entity(target_chat)
    semaphore = asyncio.Semaphore(2)

    async def download_one(idx, meta):
        del idx
        if stop_event and stop_event.is_set():
            set_task_status(progress_state, meta["id"], "stopped")
            return
        async with semaphore:
            if stop_event and stop_event.is_set():
                set_task_status(progress_state, meta["id"], "stopped")
                return
            filename = sanitize_filename(meta["filename"])
            download_rel_path = build_download_relative_path(meta)
            size_bytes = get_expected_size_bytes(meta)
            target_file_path = build_target_file_path(abs_downloads_dir, meta)
            part_path = build_partial_file_path(abs_downloads_dir, meta)
            meta_json_path = build_meta_file_path(abs_downloads_dir, meta)
            target_dir = os.path.dirname(target_file_path)

            if not os.path.realpath(target_file_path).startswith(abs_downloads_dir):
                raise ValueError(f"Rejected unsafe filename: {filename}")
            if not os.path.realpath(part_path).startswith(abs_downloads_dir):
                raise ValueError(f"Rejected unsafe filename: {filename}")

            if os.path.exists(target_file_path):
                if os.path.getsize(target_file_path) >= size_bytes:
                    for local_path in (part_path, meta_json_path):
                        try:
                            if os.path.exists(local_path):
                                os.remove(local_path)
                        except OSError:
                            pass
                    append_download_log(
                        "INFO",
                        f"Skipped already-downloaded msg_id={meta['id']} target={download_rel_path}",
                    )
                    set_task_status(progress_state, meta["id"], "skipped")
                    if progress_state is not None:
                        progress_state["files_done"] = progress_state.get("files_done", 0) + 1
                        progress_state.get("per_file_progress", {}).pop(str(meta["id"]), None)
                    return
                try:
                    os.remove(target_file_path)
                except OSError:
                    pass

            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass

            os.makedirs(target_dir, exist_ok=True)

            if progress_state is not None:
                queue_pos = 0
                for task_idx, task in enumerate(progress_state.get("task_queue", [])):
                    if task.get("id") == meta["id"] and task.get("chat", "") == target_chat:
                        queue_pos = task_idx
                        break
                progress_state.update({
                    "file_idx": queue_pos,
                    "filename": filename,
                    "bytes_done": 0,
                    "bytes_total": max(size_bytes, 1),
                    "chunks_done": 0,
                    "chunks_total": max(1, -(-size_bytes // _REQUEST_SIZE)),
                    "speed_mbps": 0.0,
                })
            set_task_status(progress_state, meta["id"], "downloading")
            append_download_log(
                "INFO",
                f"Starting msg_id={meta['id']} chat={target_chat} target={download_rel_path}",
            )

            msg = await client.get_messages(chat_entity, ids=meta["id"])

            if not os.path.exists(meta_json_path):
                with open(meta_json_path, "w") as meta_file:
                    json.dump({
                        "msg_id": meta["id"],
                        "chat": target_chat,
                        "filename": filename,
                        "size_bytes": size_bytes,
                        "total_size": size_bytes,
                        "date": meta.get("date", ""),
                        "size_mb": meta.get("size_mb", 0),
                        "topic_id": meta.get("topic_id", 0),
                        "topic_name": meta.get("topic_name", ""),
                        "download_rel_path": download_rel_path,
                    }, meta_file)

            def on_progress(chunks_done, chunks_total, bytes_done, bytes_total, speed):
                if progress_state is not None:
                    pfp = progress_state.setdefault("per_file_progress", {})
                    pfp[str(meta["id"])] = {
                        "bytes_done": bytes_done,
                        "bytes_total": max(bytes_total, 1),
                        "speed_mbps": round(speed / 1048576, 2),
                        "filename": filename,
                    }
                    progress_state["bytes_done"] = sum(v["bytes_done"] for v in pfp.values())
                    progress_state["bytes_total"] = max(sum(v["bytes_total"] for v in pfp.values()), 1)
                    progress_state["speed_mbps"] = round(sum(v["speed_mbps"] for v in pfp.values()), 2)
                    progress_state["chunks_done"] = chunks_done
                    progress_state["chunks_total"] = chunks_total
                    progress_state["filename"] = filename

            await download_video_parallel(
                client,
                msg.video,
                part_path,
                workers=file_workers,
                stop_event=stop_event,
                on_progress=on_progress,
            )

            part_is_complete = (
                os.path.exists(part_path)
                and os.path.getsize(part_path) >= size_bytes
            )

            if part_is_complete and not (stop_event and stop_event.is_set()):
                os.replace(part_path, target_file_path)

            if os.path.exists(target_file_path) and os.path.getsize(target_file_path) >= size_bytes:
                try:
                    os.remove(meta_json_path)
                except OSError:
                    pass
                append_download_log(
                    "INFO",
                    f"Completed msg_id={meta['id']} saved={download_rel_path} size_bytes={size_bytes}",
                )
                set_task_status(progress_state, meta["id"], "done")
                if progress_state is not None:
                    progress_state["files_done"] = progress_state.get("files_done", 0) + 1
                    progress_state.get("per_file_progress", {}).pop(str(meta["id"]), None)
                await asyncio.sleep(1.5)
            else:
                if stop_event and stop_event.is_set():
                    append_download_log(
                        "INFO",
                        f"Stopped msg_id={meta['id']} partial={os.path.exists(part_path)} target={download_rel_path}",
                    )
                    set_task_status(progress_state, meta["id"], "stopped")
                    if progress_state is not None:
                        progress_state.get("per_file_progress", {}).pop(str(meta["id"]), None)
                    return

                final_size = os.path.getsize(target_file_path) if os.path.exists(target_file_path) else 0
                part_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
                failure_reason = (
                    f"Download incomplete: target_size={final_size} part_size={part_size} expected={size_bytes}"
                )
                append_download_log(
                    "ERROR",
                    f"Failed msg_id={meta['id']} target={download_rel_path} reason={failure_reason}",
                )
                set_task_status(progress_state, meta["id"], "error", failure_reason)
                if progress_state is not None:
                    progress_state.get("per_file_progress", {}).pop(str(meta["id"]), None)

    try:
        await asyncio.gather(*[
            download_one(idx, meta) for idx, meta in enumerate(video_meta_list)
        ])
    finally:
        await client.disconnect()


def start_download_worker(
    api_id, api_hash, session_name, vault_path, downloads_path, file_workers,
    stop_event, progress_state, parallel_downloads=3,
):
    if progress_state.get("active"):
        return

    progress_state["active"] = True
    progress_state["error"] = None
    sync_progress_summary(progress_state)

    def bg_queue_worker():
        try:
            while True:
                if stop_event.is_set():
                    mark_unfinished_tasks(progress_state, "stopped")
                    break
                queued = [
                    task.copy()
                    for task in progress_state.get("task_queue", [])
                    if task.get("status") == "queued"
                ]
                if not queued:
                    break

                batch = queued[:max(1, parallel_downloads)]

                if len(batch) == 1:
                    # Single file — run inline (existing path, no extra thread overhead)
                    asyncio.run(
                        download_selected_chunks(
                            api_id, api_hash, session_name,
                            batch[0].get("chat", ""),
                            [batch[0]], vault_path, downloads_path, file_workers,
                            stop_event=stop_event, progress_state=progress_state, slot=0,
                        )
                    )
                else:
                    # Multiple files — each in its own thread + event loop + session slot
                    slot_threads = []
                    for slot, task in enumerate(batch):
                        t = threading.Thread(
                            target=lambda s=slot, tk=task: asyncio.run(
                                download_selected_chunks(
                                    api_id, api_hash, session_name,
                                    tk.get("chat", ""),
                                    [tk], vault_path, downloads_path, file_workers,
                                    stop_event=stop_event, progress_state=progress_state,
                                    slot=s,
                                )
                            ),
                            daemon=True,
                        )
                        t.start()
                        slot_threads.append(t)
                    for t in slot_threads:
                        t.join()

                sync_progress_summary(progress_state)
        except BaseException as worker_err:
            if not isinstance(worker_err, asyncio.CancelledError):
                progress_state["error"] = str(worker_err)
                append_download_log("ERROR", f"Worker error: {worker_err}")
        finally:
            if stop_event.is_set():
                mark_unfinished_tasks(progress_state, "stopped")
            progress_state["active"] = False

    threading.Thread(target=bg_queue_worker, daemon=True).start()
