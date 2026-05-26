import io
import os
import zipfile

from fastapi.responses import FileResponse
from nicegui import app as nicegui_app

from telegram_backend import DEFAULT_DOWNLOADS_DIR, is_system_file, safe_abs_path


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
            for root, _, files in os.walk(abs_folder):
                for fname in sorted(files):
                    if fname.endswith(".meta.json") or fname.endswith(".part") or is_system_file(fname):
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
