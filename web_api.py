"""AstrBot Pages and extension API for Romantic Memory."""

from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from quart import Response, jsonify, request

from astrbot.api import logger

from .main import PLUGIN_NAME
from .memory_store import DEFAULT_PERSONALITY_ID, MemoryStore
from .summary import embed


class RomanticMemoryWebApi:
    """Register memory CRUD, personality, import/export and monitoring endpoints."""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api
        register(
            f"/{PLUGIN_NAME}/personas",
            self.personas,
            ["GET"],
            "List available personas",
        )
        register(
            f"/{PLUGIN_NAME}/memories",
            self.memories,
            ["GET", "POST"],
            "Memory list and create",
        )
        register(
            f"/{PLUGIN_NAME}/memories/bulk-delete",
            self.bulk_delete,
            ["POST"],
            "Bulk delete memories",
        )
        register(
            f"/{PLUGIN_NAME}/memories/<memory_id>",
            self.memory,
            ["GET", "POST", "PATCH", "DELETE"],
            "Memory CRUD",
        )
        register(
            f"/{PLUGIN_NAME}/short-term", self.short_term, ["GET"], "Short-term memory"
        )
        register(
            f"/{PLUGIN_NAME}/short-term/personality/<personality_id>",
            self.short_term_personality,
            ["GET"],
            "Short-term memory by personality",
        )
        register(
            f"/{PLUGIN_NAME}/short-term/message",
            self.short_term_message,
            ["POST"],
            "Edit or delete short-term message",
        )
        register(
            f"/{PLUGIN_NAME}/short-term/<message_id>",
            self.short_term_message,
            ["POST", "PATCH", "DELETE"],
            "Edit or delete short-term message",
        )
        register(
            f"/{PLUGIN_NAME}/import", self.import_memories, ["POST"], "Import memories"
        )
        register(
            f"/{PLUGIN_NAME}/import_text",
            self.import_text,
            ["POST"],
            "Import memory text",
        )
        register(
            f"/{PLUGIN_NAME}/export", self.export_memories, ["GET"], "Export memories"
        )
        register(f"/{PLUGIN_NAME}/backup", self.backup, ["GET"], "Backup memories")
        register(
            f"/{PLUGIN_NAME}/monitoring", self.monitoring, ["GET"], "Memory monitoring"
        )
        register(
            f"/{PLUGIN_NAME}/profile",
            self.profile,
            ["GET", "POST"],
            "Love Memory profile",
        )

    async def _json(self) -> dict[str, Any]:
        return await request.get_json(silent=True) or {}

    def _effective_session(self, value: str | None) -> str:
        return self.plugin.sessions.key(str(value or ""))

    @staticmethod
    def _effective_personality(value: str | None) -> str:
        value = str(value or "").strip()
        return value if value and value != "[%None]" else DEFAULT_PERSONALITY_ID

    @staticmethod
    def _default_profile() -> dict[str, str]:
        return {
            "userName": "User",
            "charName": "Char",
            "startDate": "",
            "userAvatarSrc": "",
            "charAvatarSrc": "",
            "userSignature": "",
            "charSignature": "",
        }

    def _profile_path(self) -> Path:
        return Path(self.plugin.plugin_data_dir) / "love_memory_profile.json"

    def _read_profile_file(self) -> tuple[dict[str, str], bool]:
        path = self._profile_path()
        if not path.is_file():
            return self._default_profile(), False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            logger.warning(
                "Romantic Memory profile file is invalid; using defaults: %s", path
            )
            return self._default_profile(), False
        if not isinstance(raw, dict):
            return self._default_profile(), False
        default = self._default_profile()
        profile = {key: str(raw.get(key, default[key]) or "") for key in default}
        profile["userName"] = profile["userName"] or default["userName"]
        profile["charName"] = profile["charName"] or default["charName"]
        return profile, True

    def _write_profile_file(self, profile: dict[str, str]) -> None:
        path = self._profile_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    async def profile(self) -> Any:
        """Read or persist the Love Memory page profile outside the browser sandbox."""
        try:
            if request.method == "GET":
                profile, stored = await asyncio.to_thread(self._read_profile_file)
                return jsonify({"data": profile, "stored": stored})

            body = await self._json()
            default = self._default_profile()
            profile = {key: str(body.get(key, default[key]) or "") for key in default}
            profile["userName"] = profile["userName"] or default["userName"]
            profile["charName"] = profile["charName"] or default["charName"]
            await asyncio.to_thread(self._write_profile_file, profile)
            return jsonify({"data": profile, "stored": True})
        except Exception as exc:
            logger.error(
                "Romantic Memory profile operation failed: %s", exc, exc_info=True
            )
            return jsonify({"status": "error", "message": str(exc)}), 500

    async def personas(self) -> Any:
        """Return all personas known by AstrBot for the manual memory editor."""
        result: list[dict[str, str]] = []
        seen: set[str] = set()

        def add_persona(persona: Any) -> None:
            if isinstance(persona, dict):
                value = (
                    persona.get("name")
                    or persona.get("persona_id")
                    or persona.get("id")
                )
            else:
                value = (
                    getattr(persona, "persona_id", None)
                    or getattr(persona, "name", None)
                    or getattr(persona, "id", None)
                )
            persona_id = str(value or "").strip()
            if not persona_id or persona_id in seen:
                return
            seen.add(persona_id)
            result.append(
                {
                    "id": persona_id,
                    "name": "默认人格"
                    if persona_id == DEFAULT_PERSONALITY_ID
                    else persona_id,
                }
            )

        add_persona({"name": DEFAULT_PERSONALITY_ID})
        manager = getattr(self.plugin.context, "persona_manager", None)
        provider_manager = getattr(self.plugin.context, "provider_manager", None)
        for source in (
            getattr(manager, "personas_v3", None),
            getattr(manager, "personas", None),
            getattr(provider_manager, "personas", None),
        ):
            for persona in source or []:
                add_persona(persona)
        return jsonify({"data": result})

    async def memories(self) -> Any:
        try:
            if request.method == "GET":
                session_id = request.args.get("session_id")
                personality_id = request.args.get("personality_id") or None
                keyword = request.args.get("keyword", "").strip().lower()
                records = await asyncio.to_thread(
                    self.plugin.store.list_records,
                    self._effective_session(session_id) if session_id else None,
                    self._effective_personality(personality_id)
                    if personality_id
                    else None,
                )
                if keyword:
                    records = [
                        record
                        for record in records
                        if keyword in str(record.get("content", "")).lower()
                        or keyword in str(record.get("keywords", "")).lower()
                    ]
                return jsonify({"data": records, "total": len(records)})

            body = await self._json()
            content = str(body.get("content", "")).strip()
            if not content:
                return jsonify(
                    {"status": "error", "message": "content cannot be empty"}
                ), 400
            session_id = self._effective_session(str(body.get("session_id", "")))
            personality_id = self._effective_personality(body.get("personality_id"))
            vector = await embed(self.plugin, content)
            record = await asyncio.to_thread(
                self.plugin.store.add,
                content,
                vector,
                session_id,
                float(body.get("timestamp", time.time()) or time.time()),
                None,
                body.get("date"),
                personality_id,
            )
            return jsonify(record)
        except Exception as exc:
            logger.error("Romantic memory API operation failed: %s", exc, exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    async def bulk_delete(self) -> Any:
        """Delete the selected long-term memories after page-side confirmation."""
        try:
            body = await self._json()
            raw_ids = body.get("ids", [])
            if not isinstance(raw_ids, list):
                return jsonify(
                    {"status": "error", "message": "ids must be a list"}
                ), 400
            deleted = await asyncio.to_thread(self.plugin.store.delete_many, raw_ids)
            return jsonify({"deleted": deleted})
        except Exception as exc:
            logger.error("Romantic memory bulk delete failed: %s", exc, exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    async def memory(self, memory_id: str) -> Any:
        try:
            if request.method == "GET":
                record = await asyncio.to_thread(self.plugin.store.get, memory_id)
                return (
                    (jsonify(record), 200)
                    if record
                    else (
                        jsonify({"status": "error", "message": "memory not found"}),
                        404,
                    )
                )
            if request.method == "DELETE":
                deleted = await asyncio.to_thread(self.plugin.store.delete, memory_id)
                return jsonify({"deleted": deleted}), 200 if deleted else 404

            body = await self._json()
            old = await asyncio.to_thread(self.plugin.store.get, memory_id)
            if old is None:
                return jsonify({"status": "error", "message": "memory not found"}), 404
            content = str(body.get("content", old.get("content", ""))).strip()
            if not content:
                return jsonify(
                    {"status": "error", "message": "content cannot be empty"}
                ), 400
            timestamp = float(
                body.get("timestamp", old.get("timestamp", time.time())) or time.time()
            )
            session_id = self._effective_session(
                str(body.get("session_id", old.get("session_id", "")))
            )
            personality_id = self._effective_personality(
                body.get("personality_id", old.get("personality_id"))
            )
            vector = await embed(self.plugin, content)
            record = await asyncio.to_thread(
                self.plugin.store.update,
                memory_id,
                content,
                vector,
                session_id,
                timestamp,
                body.get("date") or old.get("date"),
                personality_id,
            )
            return jsonify(record)
        except Exception as exc:
            logger.error("Romantic memory API update failed: %s", exc, exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    async def short_term_personality(self, personality_id: str) -> Any:
        personality = self._effective_personality(personality_id)
        messages = await self.plugin.sessions.pending_for_personality(personality)
        return jsonify(
            {
                "session_id": "",
                "personality_id": personality,
                "messages": messages,
            }
        )

    async def short_term(self) -> Any:
        session_id = str(request.args.get("session_id", "")).strip()
        personality_id = self._effective_personality(request.args.get("personality_id"))
        if session_id:
            messages = await self.plugin.sessions.pending(session_id, personality_id)
            canonical_session = self.plugin.sessions.key(session_id)
            for message in messages:
                message["session_id"] = canonical_session
        else:
            messages = await self.plugin.sessions.pending_for_personality(
                personality_id
            )
            canonical_session = ""
        return jsonify(
            {
                "session_id": canonical_session,
                "personality_id": personality_id,
                "messages": messages,
            }
        )

    async def short_term_message(self, message_id: str | None = None) -> Any:
        try:
            body = await self._json()
            message_id = str(message_id or body.get("message_id") or "").strip()
            if not message_id:
                return jsonify(
                    {"status": "error", "message": "message_id cannot be empty"}
                ), 400
            session_id = str(
                body.get("session_id") or request.args.get("session_id") or ""
            ).strip()
            if not session_id:
                return jsonify(
                    {"status": "error", "message": "session_id cannot be empty"}
                ), 400
            personality_id = self._effective_personality(
                body.get("personality_id") or request.args.get("personality_id")
            )
            action = str(body.get("action", "update")).lower()
            if request.method == "DELETE" or action == "delete":
                deleted = await self.plugin.sessions.delete_message(
                    session_id, personality_id, message_id
                )
                return jsonify({"deleted": deleted}), 200 if deleted else 404
            content = str(body.get("content", "")).strip()
            updated = await self.plugin.sessions.update_message(
                session_id,
                personality_id,
                message_id,
                content,
            )
            if updated is None:
                return jsonify(
                    {"status": "error", "message": "short-term message not found"}
                ), 404
            return jsonify(updated)
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 409
        except Exception as exc:
            logger.error(
                "Romantic short-term message operation failed: %s", exc, exc_info=True
            )
            return jsonify({"status": "error", "message": str(exc)}), 500

    async def import_memories(self) -> Any:
        try:
            uploaded = (await request.files).get("file")
            if uploaded is None:
                return jsonify({"status": "error", "message": "missing file"}), 400
            raw = await uploaded.read()
            filename = uploaded.filename or "memory.txt"
            fmt = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
            form = await request.form
            default_session = form.get("session_id", "")
            default_personality = self._effective_personality(
                form.get("personality_id")
            )
            records = MemoryStore.parse_import(
                raw.decode("utf-8-sig"), fmt, default_session, default_personality
            )
            inserted = []
            for record in records:
                content = str(record.get("content", "")).strip()
                if not content:
                    continue
                vector = await embed(self.plugin, content)
                inserted.append(
                    await asyncio.to_thread(
                        self.plugin.store.add,
                        content,
                        vector,
                        self._effective_session(
                            str(record.get("session_id", default_session))
                        ),
                        self.plugin.store.timestamp_for_record(record),
                        str(record["id"]) if record.get("id") else None,
                        record.get("date"),
                        self._effective_personality(
                            str(record.get("personality_id", default_personality))
                        ),
                    )
                )
            return jsonify({"inserted": len(inserted), "data": inserted})
        except Exception as exc:
            logger.error("Romantic memory import failed: %s", exc, exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    async def import_text(self) -> Any:
        """Import text through the Dashboard plugin bridge without multipart fetch."""
        try:
            body = await self._json()
            content = str(body.get("content", ""))
            if not content:
                return jsonify(
                    {"status": "error", "message": "content cannot be empty"}
                ), 400
            filename = str(body.get("filename", "memory.txt"))
            fmt = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
            default_session = str(body.get("session_id", ""))
            default_personality = self._effective_personality(
                body.get("personality_id")
            )
            records = MemoryStore.parse_import(
                content, fmt, default_session, default_personality
            )
            inserted = []
            for record in records:
                record_content = str(record.get("content", "")).strip()
                if not record_content:
                    continue
                vector = await embed(self.plugin, record_content)
                inserted.append(
                    await asyncio.to_thread(
                        self.plugin.store.add,
                        record_content,
                        vector,
                        self._effective_session(
                            str(record.get("session_id", default_session))
                        ),
                        self.plugin.store.timestamp_for_record(record),
                        str(record["id"]) if record.get("id") else None,
                        record.get("date"),
                        self._effective_personality(
                            str(record.get("personality_id", default_personality))
                        ),
                    )
                )
            return jsonify({"data": {"inserted": len(inserted), "data": inserted}})
        except Exception as exc:
            logger.error("Romantic memory text import failed: %s", exc, exc_info=True)
            return jsonify({"status": "error", "message": str(exc)}), 500

    async def export_memories(self) -> Response:
        fmt = request.args.get("format", "json").lower()
        session_id = request.args.get("session_id")
        personality_id = request.args.get("personality_id") or None
        records = await asyncio.to_thread(
            self.plugin.store.export_records,
            self._effective_session(session_id) if session_id else None,
            self._effective_personality(personality_id) if personality_id else None,
        )
        if fmt == "json":
            body = json.dumps({"memories": records}, ensure_ascii=False, indent=2)
            content_type, suffix = "application/json; charset=utf-8", "json"
        elif fmt == "md":
            body = "\n".join(
                f"- {item.get('date', '')} | [{item.get('personality_id', DEFAULT_PERSONALITY_ID)}] {item.get('content', '')}"
                for item in records
            )
            content_type, suffix = "text/markdown; charset=utf-8", "md"
        else:
            body = "\n".join(
                f"{item.get('date', '')} | [{item.get('personality_id', DEFAULT_PERSONALITY_ID)}] {item.get('content', '')}"
                for item in records
            )
            content_type, suffix = "text/plain; charset=utf-8", "txt"
        return Response(
            body,
            content_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename=romantic_memories.{suffix}"
            },
        )

    async def backup(self) -> Response:
        records = await asyncio.to_thread(self.plugin.store.export_records)
        sessions = {
            key: {
                "messages": state.messages,
                "last_activity": state.last_activity,
                "rounds": state.rounds,
                "personality_id": state.personality_id,
            }
            for key, state in self.plugin.sessions.all_states().items()
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "memories.json",
                json.dumps({"memories": records}, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "short_term.json", json.dumps(sessions, ensure_ascii=False, indent=2)
            )
            archive.writestr(
                "plugin_config.json",
                json.dumps(dict(self.plugin.config), ensure_ascii=False, indent=2),
            )
        buffer.seek(0)
        return Response(
            buffer.read(),
            content_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=romantic_memory_backup.zip"
            },
        )

    async def monitoring(self) -> Any:
        long_term_memory_count = 0
        long_term_personalities: dict[str, int] = {}
        long_term_sessions: dict[str, int] = {}
        long_term_memory_error = ""
        if self.plugin.store.collection is not None:
            try:
                records = await asyncio.to_thread(self.plugin.store.list_records)
                long_term_memory_count = len(records)
                for record in records:
                    personality_id = str(
                        record.get("personality_id", DEFAULT_PERSONALITY_ID)
                    )
                    session_id = str(record.get("session_id", ""))
                    long_term_personalities[personality_id] = (
                        long_term_personalities.get(personality_id, 0) + 1
                    )
                    long_term_sessions[session_id] = (
                        long_term_sessions.get(session_id, 0) + 1
                    )
            except Exception as exc:
                long_term_memory_error = str(exc)
                logger.warning(
                    "Romantic memory monitoring could not list long-term memories: %s",
                    exc,
                )
        return jsonify(
            {
                "chroma_path": str(self.plugin.vector_path),
                "chroma_connected": self.plugin.store.collection is not None,
                "embedding_provider_available": bool(
                    self.plugin.context.get_all_embedding_providers()
                ),
                "llm_provider_available": self.plugin.context.get_using_provider(None)
                is not None,
                "long_term_memory_count": long_term_memory_count,
                "long_term_personalities": long_term_personalities,
                "long_term_sessions": long_term_sessions,
                "long_term_memory_error": long_term_memory_error,
                "session_count": len(self.plugin.sessions.sessions),
                "short_term_messages": sum(
                    len(state.messages)
                    for state in self.plugin.sessions.sessions.values()
                ),
                "metrics": self.plugin.metrics,
            }
        )
