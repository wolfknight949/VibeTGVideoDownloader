import json
import os
import re
from datetime import datetime

_SAFE_ROOT = os.path.realpath(os.getcwd())

DEFAULT_DOWNLOADS_DIR = "downloads"
DOWNLOAD_LOG_FILE = "telegram_downloads.log"

_SKIP_FILENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def is_system_file(fname: str) -> bool:
    return fname in _SKIP_FILENAMES or fname.startswith("._")


def safe_abs_path(user_path: str) -> str:
    resolved = os.path.realpath(os.path.abspath(user_path))
    if not resolved.startswith(_SAFE_ROOT):
        raise ValueError(
            f"Download path '{user_path}' must be inside the project directory."
        )
    return resolved


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


def scan_downloaded_files(out_dir: str) -> set:
    downloaded = set()
    try:
        abs_dir = safe_abs_path(out_dir)
        if not os.path.isdir(abs_dir):
            return downloaded
        for root, dirs, files in os.walk(abs_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                full_path = os.path.join(root, fname)
                if not os.path.isfile(full_path):
                    continue
                if (
                    fname.endswith(".meta.json") or fname.endswith(".part")
                    or fname.endswith(".thumb") or is_system_file(fname)
                ):
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
        for root, dirs, files in os.walk(abs_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in sorted(files):
                if (
                    fname.endswith(".meta.json") or fname.endswith(".part")
                    or fname.endswith(".thumb") or is_system_file(fname)
                ):
                    continue
                full_path = os.path.join(root, fname)
                if not os.path.isfile(full_path):
                    continue
                rel_path = os.path.relpath(full_path, abs_dir)
                folder = os.path.dirname(rel_path) or ""
                size_bytes = os.path.getsize(full_path)
                # Read thumbnail sidecar written by the downloader on completion
                msg_id, chat = 0, ""
                thumb_sidecar = full_path + ".thumb"
                if os.path.isfile(thumb_sidecar):
                    try:
                        with open(thumb_sidecar) as _sf:
                            _td = json.load(_sf)
                            msg_id = _td.get("msg_id", 0)
                            chat   = _td.get("chat", "")
                    except Exception:
                        pass
                result.append({
                    "rel_path": rel_path,
                    "filename": fname,
                    "folder": folder,
                    "size_mb": round(size_bytes / 1048576, 2),
                    "abs_path": full_path,
                    "msg_id": msg_id,
                    "chat": chat,
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
