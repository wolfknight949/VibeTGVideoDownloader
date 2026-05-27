"""
telegram_backend — public API.

All symbols previously importable from the flat telegram_backend.py module
remain importable from this package unchanged.
"""

from .filesystem import (
    DEFAULT_DOWNLOADS_DIR,
    append_download_log,
    build_download_relative_path,
    build_meta_file_path,
    build_partial_file_path,
    build_resume_task,
    build_download_task,
    build_target_file_path,
    delete_downloaded_file,
    get_expected_size_bytes,
    get_topic_folder_name,
    is_system_file,
    is_video_downloaded,
    list_downloaded_files,
    safe_abs_path,
    sanitize_filename,
    sanitize_folder_name,
    scan_downloaded_files,
    scan_incomplete_downloads,
)
from .state import (
    TASK_STATUS_ICONS,
    add_recent_group,
    apply_downloaded_flags,
    build_posts,
    create_app_state,
    ensure_session_defaults,
    filter_completed_incomplete,
    filter_visible_incomplete,
    load_recent_groups,
    mark_unfinished_tasks,
    queue_downloads,
    remove_from_queue,
    requeue_task,
    remove_recent_group,
    reset_scan_state,
    set_task_status,
    sync_progress_summary,
)
from .client import (
    create_telegram_client,
    prepare_client_session,
    resolve_group_title,
)
from .scanner import (
    PAGE_SIZE,
    build_video_record,
    fetch_forum_topics,
    fetch_group_videos,
    fetch_topic_videos,
)
from .downloader import (
    download_selected_chunks,
    download_video_parallel,
    start_download_worker,
)

__all__ = [
    # filesystem
    "DEFAULT_DOWNLOADS_DIR",
    "append_download_log",
    "build_download_relative_path",
    "build_meta_file_path",
    "build_partial_file_path",
    "build_resume_task",
    "build_download_task",
    "build_target_file_path",
    "delete_downloaded_file",
    "get_expected_size_bytes",
    "get_topic_folder_name",
    "is_system_file",
    "is_video_downloaded",
    "list_downloaded_files",
    "safe_abs_path",
    "sanitize_filename",
    "sanitize_folder_name",
    "scan_downloaded_files",
    "scan_incomplete_downloads",
    # state
    "TASK_STATUS_ICONS",
    "add_recent_group",
    "apply_downloaded_flags",
    "build_posts",
    "create_app_state",
    "ensure_session_defaults",
    "filter_completed_incomplete",
    "filter_visible_incomplete",
    "load_recent_groups",
    "mark_unfinished_tasks",
    "queue_downloads",
    "remove_from_queue",
    "remove_recent_group",
    "reset_scan_state",
    "set_task_status",
    "sync_progress_summary",
    # client
    "create_telegram_client",
    "prepare_client_session",
    "resolve_group_title",
    # scanner
    "PAGE_SIZE",
    "build_video_record",
    "fetch_forum_topics",
    "fetch_group_videos",
    "fetch_topic_videos",
    # downloader
    "download_selected_chunks",
    "download_video_parallel",
    "start_download_worker",
]
