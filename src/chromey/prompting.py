from __future__ import annotations


def format_recent_messages(messages: list[dict[str, object]], *, current_request: str, limit: int = 10) -> str:
    parts: list[str] = []
    for item in messages[-limit:]:
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant", "system"} or not content:
            continue
        parts.append(f"{role}: {content}")
    if current_request.strip():
        parts.append(f"user: {current_request.strip()}")
    return "\n".join(parts)


def extract_latest_user_text(messages: list[dict[str, object]]) -> str:
    for item in reversed(messages):
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role == "user" and content:
            return content
    return ""


def messages_before_latest_user(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    for index in range(len(messages) - 1, -1, -1):
        role = str(messages[index].get("role") or "").strip().lower()
        content = str(messages[index].get("content") or "").strip()
        if role == "user" and content:
            return messages[:index]
    return messages
