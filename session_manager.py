"""Persistent short-term conversation records with personality isolation."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

_CONTEXT_METADATA_MARKER = (
    "\u0055\u0073\u0065\u0072\u53d1\u9001\u5f53\u5730\u65f6\u95f4"
)
_CONTEXT_METADATA_RE = re.compile(
    r"^\s*\[" + re.escape(_CONTEXT_METADATA_MARKER) + r".*?\]\s*\$?\s*",
    re.DOTALL,
)


def parse_filter_terms(value: Any) -> tuple[str, ...]:
    """Normalize comma/newline/semicolon separated custom metadata markers."""
    if isinstance(value, (list, tuple, set)):
        values = [str(item or "") for item in value]
    else:
        values = re.split(r"[,\uFF0C;\uFF1B\r\n]+", str(value or ""))
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))


def strip_context_metadata(content: str, filter_terms: Any = None) -> str:
    """Remove the default or configured leading metadata block from text."""
    text = _CONTEXT_METADATA_RE.sub("", str(content or ""), count=1).strip()
    terms = parse_filter_terms(filter_terms)
    if not terms:
        return text

    def marked(prefix: str) -> bool:
        candidate = prefix.casefold()
        return any(term.casefold() in candidate for term in terms)

    closing = text.find("]")
    if closing >= 0 and marked(text[: closing + 1]):
        return text[closing + 1 :].lstrip(" \\t$|:")
    dollar = text.find("$")
    if dollar >= 0 and marked(text[:dollar]):
        return text[dollar + 1 :].lstrip(" \\t|:")
    newline = re.search(r"\r?\n", text)
    if newline and marked(text[: newline.start()]):
        return text[newline.end() :].lstrip()
    return text


@dataclass
class SessionState:
    """Metadata and a synchronized view of one persisted private conversation."""

    session_id: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_activity: float = 0.0
    rounds: int = 0
    last_system_prompt: str = ""
    personality_id: str = "default"
    summary_in_progress: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionManager:
    """Manage persistent short-term buffers; summary snapshots read the files."""

    def __init__(
        self,
        isolated: bool = True,
        storage_path: Path | None = None,
        filter_terms: Any = None,
    ) -> None:
        self.isolated = isolated
        self.shared_key = "__romantic_shared__"
        self.storage_path = Path(storage_path) if storage_path else None
        self.filter_terms = parse_filter_terms(filter_terms)
        self.sessions: dict[str, SessionState] = {}
        self._guard = asyncio.Lock()

    def key(self, session_id: str) -> str:
        return session_id if self.isolated else self.shared_key

    @staticmethod
    def _personality(value: str | None) -> str:
        value = str(value or "").strip()
        return value if value and value != "[%None]" else "default"

    def _cache_key(self, session_id: str, personality_id: str) -> str:
        return f"{self.key(session_id)}::{self._personality(personality_id)}"

    def _file_path(self, session_id: str, personality_id: str) -> Path | None:
        if self.storage_path is None:
            return None
        canonical_session = self.key(session_id)
        personality = self._personality(personality_id)
        return (
            self.storage_path
            / quote(personality, safe="")
            / f"{quote(canonical_session, safe='')}.json"
        )

    def _normalize_message(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        role = str(raw.get("role", "")).strip().lower()
        message_type = str(raw.get("type", "text") or "text").strip().lower()
        if role not in {"user", "assistant"} or message_type in {
            "think",
            "thinking",
            "reasoning",
        }:
            return None
        content = raw.get("content", "")
        if not isinstance(content, str):
            content = str(content or "")
        content = strip_context_metadata(content, self.filter_terms)
        if not content:
            return None
        try:
            timestamp = float(raw.get("timestamp", time.time()) or time.time())
        except (TypeError, ValueError):
            timestamp = time.time()
        return {
            "id": str(raw.get("id") or uuid.uuid4().hex),
            "role": role,
            "type": "text",
            "content": content,
            "timestamp": timestamp,
        }

    def _empty_state(self, session_id: str, personality_id: str) -> SessionState:
        return SessionState(
            session_id=self.key(session_id),
            personality_id=self._personality(personality_id),
            last_activity=time.time(),
        )

    def _read_state_sync(self, session_id: str, personality_id: str) -> SessionState:
        path = self._file_path(session_id, personality_id)
        state = self._empty_state(session_id, personality_id)
        if path is None or not path.exists():
            return state
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return state
        if not isinstance(payload, dict):
            return state
        state.session_id = str(payload.get("session_id") or state.session_id)
        state.personality_id = self._personality(
            str(payload.get("personality_id") or personality_id)
        )
        state.last_system_prompt = str(payload.get("last_system_prompt", "") or "")
        try:
            state.last_activity = float(
                payload.get("last_activity", time.time()) or time.time()
            )
        except (TypeError, ValueError):
            state.last_activity = time.time()
        state.messages = [
            message
            for raw in payload.get("messages", [])
            if (message := self._normalize_message(raw)) is not None
        ]
        state.rounds = sum(
            1 for message in state.messages if message.get("role") == "assistant"
        )
        return state

    def _write_state_sync(
        self, state: SessionState, messages: list[dict[str, Any]] | None = None
    ) -> None:
        path = self._file_path(state.session_id, state.personality_id)
        if path is None:
            return
        current = state.messages if messages is None else messages
        if not current:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "session_id": state.session_id,
            "personality_id": state.personality_id,
            "last_activity": state.last_activity,
            "last_system_prompt": state.last_system_prompt,
            "messages": current,
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def _refresh_state_sync(self, state: SessionState) -> None:
        if self.storage_path is None:
            return
        fresh = self._read_state_sync(state.session_id, state.personality_id)
        state.messages = fresh.messages
        state.last_activity = fresh.last_activity
        state.rounds = fresh.rounds
        state.last_system_prompt = fresh.last_system_prompt

    async def get(
        self, session_id: str, personality_id: str | None = None
    ) -> SessionState:
        personality = self._personality(personality_id)
        cache_key = self._cache_key(session_id, personality)
        async with self._guard:
            if cache_key not in self.sessions:
                self.sessions[cache_key] = self._read_state_sync(
                    session_id, personality
                )
            return self.sessions[cache_key]

    async def restore(self) -> int:
        """Load persisted pending conversations so idle/round triggers survive restart."""
        if self.storage_path is None or not self.storage_path.exists():
            return 0
        restored = 0
        async with self._guard:
            for path in self.storage_path.glob("*/*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    session_id = str(payload.get("session_id", ""))
                    personality_id = self._personality(
                        str(payload.get("personality_id", "default"))
                    )
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
                if not session_id:
                    continue
                state = self._read_state_sync(session_id, personality_id)
                if not state.messages:
                    continue
                self.sessions[self._cache_key(session_id, personality_id)] = state
                restored += 1
        return restored

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        system_prompt: str | None = None,
        personality_id: str | None = None,
    ) -> SessionState:
        personality = self._personality(personality_id)
        state = await self.get(session_id, personality)
        message = self._normalize_message(
            {"role": role, "type": "text", "content": content}
        )
        if message is None:
            return state
        now = time.time()
        message["timestamp"] = now
        state.messages.append(message)
        state.last_activity = now
        if message["role"] == "assistant":
            state.rounds += 1
        if system_prompt is not None:
            state.last_system_prompt = system_prompt
        self._write_state_sync(state)
        return state

    async def snapshot(
        self, session_id: str, personality_id: str | None = None
    ) -> tuple[SessionState, list[dict[str, Any]]]:
        state = await self.get(session_id, personality_id)
        self._refresh_state_sync(state)
        await state.lock.acquire()
        if state.summary_in_progress or not state.messages:
            state.lock.release()
            return state, []
        state.summary_in_progress = True
        return state, [dict(item) for item in state.messages]

    def commit_summary(self, state: SessionState, count: int) -> None:
        remaining = state.messages[count:]
        self._write_state_sync(state, remaining)
        state.messages = remaining
        state.rounds = sum(1 for item in remaining if item.get("role") == "assistant")
        state.summary_in_progress = False
        state.lock.release()

    def rollback_summary(self, state: SessionState) -> None:
        state.summary_in_progress = False
        state.lock.release()

    async def pending(
        self, session_id: str, personality_id: str | None = None
    ) -> list[dict[str, Any]]:
        state = await self.get(session_id, personality_id)
        self._refresh_state_sync(state)
        return [dict(item) for item in state.messages]

    async def pending_for_personality(
        self, personality_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all pending messages for one personality, tagged with their session."""
        personality = self._personality(personality_id)
        result: list[dict[str, Any]] = []
        for state in list(self.sessions.values()):
            if state.personality_id != personality:
                continue
            records = await self.pending(state.session_id, personality)
            for item in records:
                record = dict(item)
                record["session_id"] = state.session_id
                result.append(record)
        return result

    async def update_message(
        self,
        session_id: str,
        personality_id: str,
        message_id: str,
        content: str,
    ) -> dict[str, Any] | None:
        state = await self.get(session_id, personality_id)
        self._refresh_state_sync(state)
        if state.summary_in_progress:
            raise RuntimeError("summary is in progress")
        content = strip_context_metadata(str(content or ""), self.filter_terms)
        if not content:
            raise ValueError("content cannot be empty")
        for message in state.messages:
            if str(message.get("id")) == str(message_id):
                message["content"] = content
                self._write_state_sync(state)
                return dict(message)
        return None

    async def delete_message(
        self, session_id: str, personality_id: str, message_id: str
    ) -> bool:
        state = await self.get(session_id, personality_id)
        self._refresh_state_sync(state)
        if state.summary_in_progress:
            raise RuntimeError("summary is in progress")
        before = len(state.messages)
        state.messages = [
            item for item in state.messages if str(item.get("id")) != str(message_id)
        ]
        if len(state.messages) == before:
            return False
        state.rounds = sum(
            1 for item in state.messages if item.get("role") == "assistant"
        )
        self._write_state_sync(state)
        return True

    def all_states(self) -> dict[str, SessionState]:
        return dict(self.sessions)
