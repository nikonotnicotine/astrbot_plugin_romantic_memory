"""ChromaDB persistence with session and personality isolation."""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .retrieval import extract_keywords


DEFAULT_PERSONALITY_ID = "default"


class MemoryStore:
    """ChromaDB adapter for logical memories."""

    def __init__(self, path: Path, collection_name: str) -> None:
        self.path = path
        self.collection_name = collection_name
        self.client: Any = None
        self.collection: Any = None
        self.repaired_date_timestamps = 0

    def connect(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:
            raise RuntimeError("chromadb is not installed; install the plugin requirements.txt") from exc
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.path), settings=Settings(anonymized_telemetry=False))
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self.repaired_date_timestamps = self.repair_date_timestamps()

    def _ensure(self) -> Any:
        if self.collection is None:
            self.connect()
        return self.collection

    @staticmethod
    def _date_for_timestamp(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")

    @staticmethod
    def _timestamp_for_date(date: str | None) -> float | None:
        value = str(date or "").strip()[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").timestamp()
        except ValueError:
            return None

    @classmethod
    def timestamp_for_record(cls, record: dict[str, Any], default: float | None = None) -> float:
        date_timestamp = cls._timestamp_for_date(record.get("date"))
        if date_timestamp is not None:
            return date_timestamp
        raw_timestamp = record.get("timestamp")
        if raw_timestamp is not None and str(raw_timestamp).strip():
            try:
                return float(raw_timestamp)
            except (TypeError, ValueError):
                pass
        return time.time() if default is None else default

    def repair_date_timestamps(self) -> int:
        """Repair imported records whose timestamp disagrees with their date."""
        if self.collection is None:
            return 0
        result = self.collection.get(include=["metadatas"])
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        repaired = 0
        for memory_id, metadata in zip(ids, metadatas):
            metadata = dict(metadata or {})
            date_text = str(metadata.get("date", "")).strip()[:10]
            date_timestamp = self._timestamp_for_date(date_text)
            if date_timestamp is None:
                continue
            try:
                current_timestamp = float(metadata.get("timestamp", 0) or 0)
            except (TypeError, ValueError):
                current_timestamp = 0.0
            if self._date_for_timestamp(current_timestamp) == date_text:
                continue
            metadata["timestamp"] = date_timestamp
            self.collection.update(ids=[str(memory_id)], metadatas=[metadata])
            repaired += 1
        return repaired

    @staticmethod
    def _personality(value: str | None) -> str:
        value = str(value or "").strip()
        return value if value and value != "[%None]" else DEFAULT_PERSONALITY_ID

    @classmethod
    def _where(cls, session_id: str | None, personality_id: str | None) -> dict[str, Any] | None:
        clauses = []
        if session_id:
            clauses.append({"session_id": session_id})
        if personality_id:
            clauses.append({"personality_id": cls._personality(personality_id)})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def add(
        self,
        content: str,
        embedding: list[float],
        session_id: str,
        timestamp: float | None = None,
        memory_id: str | None = None,
        date: str | None = None,
        personality_id: str | None = None,
    ) -> dict[str, Any]:
        timestamp = timestamp if timestamp is not None else (self._timestamp_for_date(date) or time.time())
        personality_id = self._personality(personality_id)
        record = {
            "id": memory_id or str(uuid.uuid4()),
            "session_id": session_id,
            "personality_id": personality_id,
            "date": date or self._date_for_timestamp(timestamp),
            "content": content.strip(),
            "timestamp": timestamp,
            "created_at": time.time(),
            "keywords": " ".join(extract_keywords(content)[:80]),
        }
        metadata = {key: value for key, value in record.items() if key not in {"id", "content"}}
        self._ensure().add(ids=[record["id"]], embeddings=[embedding], documents=[record["content"]], metadatas=[metadata])
        return record

    @staticmethod
    def _normalize(result: dict[str, Any], index: int, distance: float | None = None) -> dict[str, Any]:
        metadata = (result.get("metadatas") or [{}])[index] or {}
        record = {
            "id": (result.get("ids") or [""])[index],
            "content": (result.get("documents") or [""])[index] or "",
            **metadata,
        }
        record.setdefault("personality_id", DEFAULT_PERSONALITY_ID)
        if distance is not None:
            record["distance"] = distance
            record["score"] = max(0.0, min(1.0, 1.0 / (1.0 + distance)))
        return record

    def query(
        self,
        query_embedding: list[float],
        session_id: str | None,
        limit: int,
        personality_id: str | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": max(1, limit),
            "include": ["metadatas", "documents", "distances"],
        }
        where = self._where(session_id, personality_id)
        if where:
            kwargs["where"] = where
        result = self._ensure().query(**kwargs)
        ids = result.get("ids") or [[]]
        if not ids or not ids[0]:
            return []
        distances = (result.get("distances") or [[]])[0]
        flat = {key: (value[0] if isinstance(value, list) else value) for key, value in result.items()}
        return [self._normalize(flat, index, distances[index]) for index in range(len(ids[0]))]

    def list_records(
        self,
        session_id: str | None = None,
        personality_id: str | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"include": ["metadatas", "documents"]}
        where = self._where(session_id, personality_id)
        if where:
            kwargs["where"] = where
        result = self._ensure().get(**kwargs)
        records = [self._normalize(result, index) for index in range(len(result.get("ids", [])))]
        return sorted(records, key=lambda item: float(item.get("timestamp", 0) or 0), reverse=True)

    def get(self, memory_id: str) -> dict[str, Any] | None:
        result = self._ensure().get(ids=[memory_id], include=["metadatas", "documents"])
        if not result.get("ids"):
            return None
        return self._normalize(result, 0)

    def update(
        self,
        memory_id: str,
        content: str,
        embedding: list[float],
        session_id: str,
        timestamp: float,
        date: str | None = None,
        personality_id: str | None = None,
    ) -> dict[str, Any]:
        old = self.get(memory_id) or {}
        personality_id = self._personality(personality_id or old.get("personality_id"))
        metadata = {
            "session_id": session_id,
            "personality_id": personality_id,
            "date": date or self._date_for_timestamp(timestamp),
            "timestamp": timestamp,
            "created_at": time.time(),
            "keywords": " ".join(extract_keywords(content)[:80]),
        }
        self._ensure().update(ids=[memory_id], embeddings=[embedding], documents=[content.strip()], metadatas=[metadata])
        return {"id": memory_id, "content": content.strip(), **metadata}

    def delete(self, memory_id: str) -> bool:
        if not self.get(memory_id):
            return False
        self._ensure().delete(ids=[memory_id])
        return True

    def delete_many(self, memory_ids: Iterable[str]) -> int:
        """Delete existing memories in one operation and return the count."""
        ids = list(dict.fromkeys(str(memory_id).strip() for memory_id in memory_ids if str(memory_id).strip()))
        if not ids:
            return 0
        existing = self._ensure().get(ids=ids, include=["metadatas"])
        existing_ids = [str(memory_id) for memory_id in existing.get("ids", [])]
        if existing_ids:
            self._ensure().delete(ids=existing_ids)
        return len(existing_ids)
    def export_records(self, session_id: str | None = None, personality_id: str | None = None) -> list[dict[str, Any]]:
        return self.list_records(session_id, personality_id)

    def import_records(self, records: Iterable[dict[str, Any]], embed) -> list[dict[str, Any]]:
        inserted = []
        for raw in records:
            content = str(raw.get("content", "")).strip()
            if not content:
                continue
            vector = embed(content)
            if hasattr(vector, "__await__"):
                raise TypeError("import_records expects a synchronous embedding callback")
            inserted.append(
                self.add(
                    content,
                    vector,
                    str(raw.get("session_id", "")),
                    self.timestamp_for_record(raw),
                    str(raw["id"]) if raw.get("id") else None,
                    str(raw["date"]) if raw.get("date") else None,
                    str(raw.get("personality_id", DEFAULT_PERSONALITY_ID)),
                )
            )
        return inserted

    @staticmethod
    def parse_import(text: str, fmt: str, default_session_id: str = "", default_personality_id: str = DEFAULT_PERSONALITY_ID) -> list[dict[str, Any]]:
        fmt = fmt.lower().lstrip(".")
        if fmt == "json":
            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("memories", [data])
            return [
                {**item, "session_id": item.get("session_id", default_session_id), "personality_id": item.get("personality_id", default_personality_id)}
                for item in data
            ]
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = re.sub(r"^[*-]\s+", "", line.strip())
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^(\d{4}-\d{2}-\d{2})\s*(?:\||\uFF1A|:)\s*(.+)$", line)
            item = {"session_id": default_session_id, "personality_id": default_personality_id}
            if match:
                item.update({"date": match.group(1), "content": match.group(2)})
            else:
                item["content"] = line
            records.append(item)
        return records
