import re
from datetime import datetime

from telethon import functions
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

from .client import create_telegram_client

PAGE_SIZE = 100
_RESOLUTION_HINT_RE = re.compile(r"(?<!\d)(2160|1440|1080|720|576|540|480|360|240)p(?!\d)", re.IGNORECASE)
_STANDARD_HEIGHTS = frozenset({2160, 1440, 1080, 720, 576, 540, 480, 360, 240})


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
