import asyncio
import concurrent.futures
import io
import os
import zipfile

from fastapi.responses import FileResponse
from nicegui import app as nicegui_app

from telegram_backend import DEFAULT_DOWNLOADS_DIR, is_system_file, safe_abs_path

# Thread pool for Telethon thumbnail fetches — keeps them off NiceGUI's event loop
_thumb_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumb")

# Per-key asyncio locks so duplicate requests for the same thumbnail wait instead of racing
_thumb_locks: dict = {}
_thumb_registry_lock = None  # type: asyncio.Lock | None


def _get_registry_lock() -> asyncio.Lock:
    global _thumb_registry_lock
    if _thumb_registry_lock is None:
        _thumb_registry_lock = asyncio.Lock()
    return _thumb_registry_lock


def _fetch_thumb_sync(session: str, api_id: int, api_hash: str, chat: str, msg_id: int, cache_path: str) -> None:
    """
    Run in a worker thread (via _thumb_executor).
    Creates its own asyncio event loop so Telethon never touches NiceGUI's loop.
    """
    from telegram_backend.client import create_telegram_client

    async def _inner():
        client = create_telegram_client(session, api_id, api_hash, purpose="thumb")
        await client.connect()
        try:
            msg = await client.get_messages(chat, ids=msg_id)
            if msg and getattr(msg, "video", None):
                await client.download_media(msg, file=cache_path, thumb=-1)
        finally:
            await client.disconnect()

    asyncio.run(_inner())


@nicegui_app.get("/api/download-file")
async def _api_download_file(rel_path: str, dl_dir: str = DEFAULT_DOWNLOADS_DIR):
    try:
        abs_dir  = safe_abs_path(dl_dir)
        abs_path = safe_abs_path(os.path.join(abs_dir, rel_path))
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.isfile(abs_path):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        abs_path,
        filename=os.path.basename(abs_path),
        media_type="application/octet-stream",
    )


@nicegui_app.get("/api/download-folder")
async def _api_download_folder(folder: str, dl_dir: str = DEFAULT_DOWNLOADS_DIR):
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    try:
        abs_dir    = safe_abs_path(dl_dir)
        abs_folder = safe_abs_path(os.path.join(abs_dir, folder)) if folder else abs_dir
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.isdir(abs_folder):
        raise HTTPException(status_code=404, detail="Folder not found")

    def _iter_zip():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for root, dirs, files in os.walk(abs_folder):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in sorted(files):
                    if fname.endswith(".meta.json") or fname.endswith(".part") or fname.endswith(".thumb") or is_system_file(fname):
                        continue
                    full = os.path.join(root, fname)
                    arcname = os.path.relpath(full, abs_folder)
                    zf.write(full, arcname)
        buf.seek(0)
        yield from iter(lambda: buf.read(1024 * 1024), b"")

    zip_name = (os.path.basename(abs_folder) or "downloads") + ".zip"
    return StreamingResponse(
        _iter_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@nicegui_app.get("/api/thumbnail")
async def _api_thumbnail(
    msg_id: int,
    chat: str,
    session: str = "sessions/tg_parser_session",
):
    """
    Return a cached JPEG thumbnail for a Telegram video message.

    Fetched once via Telethon (thumb=-1 = largest available) and cached in
    downloads/.thumbcache/ so every subsequent request is served from disk.
    """
    from fastapi import HTTPException

    api_id   = int(os.environ.get("TG_API_ID", "0"))
    api_hash = os.environ.get("TG_API_HASH", "")
    if not api_id or not api_hash:
        raise HTTPException(status_code=503, detail="No API credentials configured — check .env")

    # Safe cache path
    safe_chat = "".join(c for c in chat if c.isalnum() or c in "-_@.")[:64]
    cache_dir  = os.path.join(DEFAULT_DOWNLOADS_DIR, ".thumbcache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{safe_chat}_{msg_id}.jpg")

    # Fast path — already on disk
    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
        return FileResponse(
            cache_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # Coalesce concurrent requests for the same (chat, msg_id)
    lock_key = f"{safe_chat}_{msg_id}"
    async with _get_registry_lock():
        if lock_key not in _thumb_locks:
            _thumb_locks[lock_key] = asyncio.Lock()
    lock = _thumb_locks[lock_key]

    async with lock:
        # A prior waiter may have already written the file
        if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
            return FileResponse(
                cache_path,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                _thumb_executor,
                _fetch_thumb_sync,
                session, api_id, api_hash, chat, msg_id, cache_path,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"Thumbnail unavailable: {exc}") from exc

    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
        return FileResponse(
            cache_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    raise HTTPException(status_code=404, detail="Thumbnail not available")
