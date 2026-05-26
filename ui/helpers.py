import re
from typing import Optional


_HASHTAG_RE = re.compile(r"#\w+")


def extract_hashtags(text: str) -> list:
    return _HASHTAG_RE.findall(text or "")


def get_post_title(post: dict) -> str:
    if post.get("description"):
        first_line = post["description"].splitlines()[0].strip()
        if first_line:
            return first_line.split(".", 1)[0].strip() or first_line
    return post["videos"][0]["filename"]


def format_eta(secs: Optional[float]) -> str:
    if secs is None or secs < 0:
        return "--"
    t = int(round(secs))
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def topic_label_of(post: dict) -> str:
    name = (post.get("topic_name") or "").strip()
    tid  = post.get("topic_id", 0)
    return name or (f"Topic #{tid}" if tid else "")


def group_by_topic(posts: dict) -> list:
    buckets: dict = {}
    for gid, post in posts.items():
        buckets.setdefault(post.get("topic_id", 0), []).append((gid, post))
    return list(buckets.items())


def topic_targets(topic_posts: list) -> list:
    return [
        v for _, p in topic_posts
        if not p.get("downloaded")
        for v in p["videos"]
        if not v.get("downloaded")
    ]


def checked_targets(posts: dict, post_selections: dict) -> list:
    return [
        v for gid, p in posts.items()
        if post_selections.get(gid) and not p.get("downloaded")
        for v in p["videos"]
        if not v.get("downloaded")
    ]


def merge_videos(existing: list, new: list) -> list:
    ids = {v["id"] for v in existing}
    return list(existing) + [v for v in new if v["id"] not in ids]


def merge_topics(existing: list, new: list) -> list:
    m = {t["topic_id"]: dict(t) for t in existing}
    for t in new:
        e = dict(t)
        e["loaded"] = m.get(t["topic_id"], {}).get("loaded", False) or t.get("loaded", False)
        m[t["topic_id"]] = e
    return sorted(m.values(), key=lambda t: (t.get("last_update_ts", ""), t.get("topic_id", 0)), reverse=True)


def has_cursor(c: dict) -> bool:
    return bool((c or {}).get("offset_topic"))


def mark_loaded(state: dict, topic_id: int) -> None:
    for t in state["forum_topics"]:
        if t.get("topic_id") == topic_id:
            t["loaded"] = True
            break
