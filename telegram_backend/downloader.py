import asyncio
import json
import os
import threading
import time
from collections import deque

from .client import create_telegram_client
from .filesystem import (
    append_download_log,
    build_download_relative_path,
    build_meta_file_path,
    build_partial_file_path,
    build_target_file_path,
    get_expected_size_bytes,
    safe_abs_path,
    sanitize_filename,
)
from .state import mark_unfinished_tasks, set_task_status, sync_progress_summary

_REQUEST_SIZE = 1024 * 1024  # 1 MB per chunk (Telethon maximum)


def _preallocate_file(path: str, size: int) -> None:
    with open(path, "wb") as file_handle:
        file_handle.seek(size - 1)
        file_handle.write(b"\x00")


def _write_with_handle(fh, offset: int, data: bytes) -> None:
    fh.seek(offset)
    fh.write(data)


async def download_video_parallel(
    client, media, out_path: str, workers: int = 2,
    stop_event=None, on_progress=None,
    skip_worker_starts=(),
    on_worker_complete=None,
    initial_bytes_done: int = 0,
    worker_resume_chunks: dict = None,   # {start_chunk: chunks_already_done}
    on_worker_progress=None,             # (start_chunk, chunks_done, total_bytes_done)
) -> None:
    total = media.size
    total_chunks = -(-total // _REQUEST_SIZE)
    chunks_per_worker = -(-total_chunks // workers)

    loop = asyncio.get_running_loop()
    # Only preallocate when the .part file isn't already the right size (resume case)
    if not (os.path.exists(out_path) and os.path.getsize(out_path) >= total):
        await loop.run_in_executor(None, _preallocate_file, out_path, total)

    chunks_done = [initial_bytes_done // _REQUEST_SIZE]
    bytes_done = [initial_bytes_done]
    speed_samples: deque = deque(maxlen=30)

    async def fetch_segment(start_chunk: int):
        if start_chunk in skip_worker_starts:
            return  # already completed in a previous run
        resume_count = (worker_resume_chunks or {}).get(start_chunk, 0)
        offset = (start_chunk + resume_count) * _REQUEST_SIZE
        limit = min(chunks_per_worker, total_chunks - start_chunk) - resume_count
        if limit <= 0:
            # Segment fully downloaded in a prior run but not marked complete
            if on_worker_complete:
                on_worker_complete(start_chunk)
            return
        local_chunks = resume_count
        fh = open(out_path, "r+b")  # one handle per segment; closed in finally
        try:
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
                await loop.run_in_executor(None, _write_with_handle, fh, write_offset, data)
                chunks_done[0] += 1
                bytes_done[0] += len(data)
                local_chunks += 1
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
        except asyncio.CancelledError:
            # Save per-worker progress so resume can continue from here
            if on_worker_progress:
                on_worker_progress(start_chunk, local_chunks, bytes_done[0])
            raise
        finally:
            try:
                fh.close()
            except OSError:
                pass
        if on_worker_complete:
            on_worker_complete(start_chunk)

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

            # Resume support: if a .part file + meta.json exist, read completed workers
            completed_worker_starts: list = []
            initial_bytes_done: int = 0
            worker_chunk_progress: dict = {}   # {start_chunk: chunks_done_so_far}
            if os.path.exists(part_path) and os.path.exists(meta_json_path):
                try:
                    with open(meta_json_path) as _rmf:
                        _rmeta = json.load(_rmf)
                    completed_worker_starts = list(_rmeta.get("completed_worker_starts", []))
                    initial_bytes_done = int(_rmeta.get("bytes_downloaded", 0))
                    worker_chunk_progress = {
                        int(k): int(v)
                        for k, v in _rmeta.get("worker_chunk_progress", {}).items()
                    }
                except (json.JSONDecodeError, OSError, ValueError):
                    # Corrupt meta — start fresh
                    completed_worker_starts = []
                    initial_bytes_done = 0
                    worker_chunk_progress = {}
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass
            else:
                # No valid resume state — delete any stale partial file
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
                    "bytes_done": initial_bytes_done,
                    "bytes_total": max(size_bytes, 1),
                    "chunks_done": initial_bytes_done // _REQUEST_SIZE,
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
                        "completed_worker_starts": [],
                        "bytes_downloaded": 0,
                        "worker_chunk_progress": {},
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

            _total_chunks_n = -(-size_bytes // _REQUEST_SIZE)
            _cpw_n = -(-_total_chunks_n // file_workers)

            def on_worker_complete(start_chunk: int) -> None:
                limit = min(_cpw_n, _total_chunks_n - start_chunk)
                worker_bytes = min(limit * _REQUEST_SIZE,
                                   max(0, size_bytes - start_chunk * _REQUEST_SIZE))
                completed_worker_starts.append(start_chunk)
                # Remove from in-progress tracking now that it's fully done
                worker_chunk_progress.pop(start_chunk, None)
                try:
                    with open(meta_json_path) as _wf:
                        _wmd = json.load(_wf)
                    _wmd["completed_worker_starts"] = completed_worker_starts[:]
                    _wmd["bytes_downloaded"] = _wmd.get("bytes_downloaded", 0) + worker_bytes
                    _wmd["worker_chunk_progress"] = {str(k): v for k, v in worker_chunk_progress.items()}
                    with open(meta_json_path, "w") as _wf:
                        json.dump(_wmd, _wf)
                except (json.JSONDecodeError, OSError):
                    pass

            def on_worker_progress(start_chunk: int, chunks_done: int, total_bytes: int) -> None:
                """Called on stop for each in-progress segment — saves resume state."""
                worker_chunk_progress[start_chunk] = chunks_done
                try:
                    with open(meta_json_path) as _wpf:
                        _wpmd = json.load(_wpf)
                    _wpmd["worker_chunk_progress"] = {str(k): v for k, v in worker_chunk_progress.items()}
                    _wpmd["bytes_downloaded"] = total_bytes
                    with open(meta_json_path, "w") as _wpf:
                        json.dump(_wpmd, _wpf)
                except (json.JSONDecodeError, OSError):
                    pass

            await download_video_parallel(
                client,
                msg.video,
                part_path,
                workers=file_workers,
                stop_event=stop_event,
                on_progress=on_progress,
                skip_worker_starts=set(completed_worker_starts),
                on_worker_complete=on_worker_complete,
                initial_bytes_done=initial_bytes_done,
                worker_resume_chunks=worker_chunk_progress or None,
                on_worker_progress=on_worker_progress,
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
                # Write a tiny sidecar so the UI can show thumbnails for completed files
                thumb_sidecar = target_file_path + ".thumb"
                try:
                    with open(thumb_sidecar, "w") as _sf:
                        json.dump({"msg_id": meta["id"], "chat": target_chat}, _sf)
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
