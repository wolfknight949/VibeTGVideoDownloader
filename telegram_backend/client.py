import os
import shutil

from telethon import TelegramClient
from telethon.network import ConnectionTcpAbridged


def _ensure_session_dir(session_name: str) -> None:
    session_dir = os.path.dirname(session_name)
    if session_dir:
        os.makedirs(session_dir, exist_ok=True)


def _session_file_path(session_name: str) -> str:
    return session_name if session_name.endswith(".session") else f"{session_name}.session"


def prepare_client_session(session_name: str, purpose: str = "") -> str:
    if not purpose:
        return session_name

    base_session = _session_file_path(session_name)
    derived_session_name = f"{session_name}__{purpose}"
    derived_session = _session_file_path(derived_session_name)

    _ensure_session_dir(derived_session_name)

    if os.path.exists(base_session) and not os.path.exists(derived_session):
        shutil.copy2(base_session, derived_session)

    if os.path.exists(derived_session):
        return derived_session_name

    return session_name


def create_telegram_client(session_name: str, api_id: int, api_hash: str, purpose: str = "") -> TelegramClient:
    client_session = prepare_client_session(session_name, purpose)
    _ensure_session_dir(client_session)
    return TelegramClient(
        client_session,
        api_id,
        api_hash,
        connection=ConnectionTcpAbridged,
        flood_sleep_threshold=60,
        request_retries=5,
    )


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
