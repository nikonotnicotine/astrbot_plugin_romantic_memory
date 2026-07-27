"""Romantic Memory Retrieval AstrBot plugin."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

from .memory_store import DEFAULT_PERSONALITY_ID, MemoryStore
from .retrieval import filter_recent_memories, format_memory_prompt, format_recent_memory_prompt, inject_request, rank_candidates, select_for_injection, truncate_text
from .session_manager import SessionManager, strip_context_metadata
from .summary import embed, resolve_embedding_provider, summarize_messages

PLUGIN_NAME = "astrbot_plugin_romantic_memory"
TOOL_NAME = "romantic_memory_save"
ACTIVE_PLUGIN: "RomanticMemoryPlugin | None" = None


@filter.llm_tool(name=TOOL_NAME)
async def romantic_memory_save(event: AstrMessageEvent) -> str:
    """Save the current private conversation into long-term romantic memory."""
    if ACTIVE_PLUGIN is None:
        return "恋爱记忆插件尚未完成初始化。"
    return await ACTIVE_PLUGIN.save_from_tool(event)


@register(
    PLUGIN_NAME,
    "Codex",
    "基于 ChromaDB 的私聊长期记忆与时间加权检索插件。",
    "1.0.0",
)
class RomanticMemoryPlugin(Star):
    """Private-session long-term memory plugin."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        global ACTIVE_PLUGIN
        ACTIVE_PLUGIN = self
        self.context = context
        self.config = config
        self.plugin_data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        configured_path = str(config.get("vector_db_path", "") or "").strip()
        self.vector_path = Path(configured_path) if configured_path else self.plugin_data_dir / "chroma"
        self.store = MemoryStore(self.vector_path, str(config.get("collection_name", "romantic_memory")))
        self.sessions = SessionManager(
            bool(config.get("enable_isolation", True)),
            self.plugin_data_dir / "short_term",
            config.get("custom_filter_terms", ""),
        )
        self.background_task: asyncio.Task | None = None
        self.summary_tasks: set[asyncio.Task] = set()
        self._internal_call = False
        self.metrics: dict[str, Any] = {
            "summaries_success": 0,
            "summaries_failed": 0,
            "recalls": 0,
            "last_error": "",
            "last_summary_at": 0.0,
            "last_recall": {
                "status": "not_run",
                "reason": "",
                "session_id": "",
                "personality_id": "",
                "query_preview": "",
                "candidate_count": 0,
                "ranked_count": 0,
                "selected_count": 0,
            "recent_count": 0,
                "semantic_candidate_count": 0,
                "semantic_selected_count": 0,
                "context_keep_days": 0,
                "selected": [],
                "at": 0.0,
            },
        }

    @staticmethod
    def _is_private(event: AstrMessageEvent) -> bool:
        message_obj = getattr(event, "message_obj", None)
        return not bool(getattr(message_obj, "group_id", None))

    def _effective_session(self, event: AstrMessageEvent) -> str:
        return self.sessions.key(str(event.unified_msg_origin))

    async def _current_personality_id(self, event: AstrMessageEvent) -> str:
        """Resolve the persona bound to the current AstrBot conversation."""
        try:
            manager = getattr(self.context, "conversation_manager", None)
            if manager is not None:
                conversation_id = await manager.get_curr_conversation_id(event.unified_msg_origin)
                conversation = await manager.get_conversation(event.unified_msg_origin, str(conversation_id))
                persona_id = getattr(conversation, "persona_id", None) if conversation else None
                if persona_id and persona_id != "[%None]":
                    return str(persona_id)
            config = self.context.get_config(umo=event.unified_msg_origin) or {}
            fallback = (config.get("provider_settings", {}) or {}).get("default_personality")
            return str(fallback or DEFAULT_PERSONALITY_ID)
        except Exception as exc:
            logger.warning("[Romantic Memory] unable to resolve current personality: %s", exc, exc_info=True)
            return DEFAULT_PERSONALITY_ID

    async def _ensure_store(self) -> None:
        if self.store.collection is None:
            await asyncio.to_thread(self.store.connect)
            logger.info(
                "[Romantic Memory] Chroma connected | path=%s | collection=%s",
                self.vector_path,
                self.store.collection_name,
            )
            if self.store.repaired_date_timestamps:
                logger.info(
                    "[Romantic Memory] repaired memory timestamps from stored dates | count=%s",
                    self.store.repaired_date_timestamps,
                )

    async def _recall(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        session_id: str,
        personality_id: str,
    ) -> None:
        keep_days = int(self.config.get("context_keep_limit", 5))
        recall = self.metrics["last_recall"] = {
            "status": "running",
            "reason": "",
            "session_id": session_id,
            "personality_id": personality_id,
            "query_preview": "",
            "candidate_count": 0,
            "ranked_count": 0,
            "selected_count": 0,
            "recent_count": 0,
            "semantic_candidate_count": 0,
            "semantic_selected_count": 0,
            "context_keep_days": keep_days,
            "selected": [],
            "at": time.time(),
        }
        query = truncate_text(
            strip_context_metadata(str(req.prompt or event.message_str or ""), self.config.get("custom_filter_terms", "")),
            int(self.config.get("max_input_length", 4000) or 4000),
        )
        recall["query_preview"] = truncate_text(query, 160)

        await self._ensure_store()
        all_records = await asyncio.to_thread(self.store.list_records, session_id, personality_id)
        recent_selected = filter_recent_memories(all_records, keep_days)
        recent_ids = {str(item.get("id", "")) for item in recent_selected}
        injected_count = 0
        selected_log: list[dict[str, Any]] = []
        semantic_template = str(self.config.get("recall_system_prompt", "{memory_text}"))

        if recent_selected:
            recent_text = format_recent_memory_prompt(recent_selected)
            inject_request(
                req,
                recent_text,
                str(self.config.get("insert_method", "user_prompt")),
                str(self.config.get("insert_position", "prepend")),
            )
            injected_count += len(recent_selected)
            selected_log.extend(
                {
                    "id": item.get("id", ""),
                    "date": item.get("date", ""),
                    "content": truncate_text(str(item.get("content", "")), 240),
                    "source": "recent",
                }
                for item in recent_selected
            )
            logger.info(
                "[Romantic Memory] injected recent memories into ProviderRequest | session=%s | personality=%s | keep_days=%s | candidates=%s | within_window=%s | selected=%s | method=%s | position=%s\n%s",
                session_id,
                personality_id,
                keep_days,
                len(all_records),
                len(recent_selected),
                len(recent_selected),
                str(self.config.get("insert_method", "user_prompt")),
                str(self.config.get("insert_position", "prepend")),
                recent_text,
            )
        else:
            logger.info(
                "[Romantic Memory] recent memory injection skipped | session=%s | personality=%s | keep_days=%s | candidates=%s | within_window=0",
                session_id,
                personality_id,
                keep_days,
                len(all_records),
            )

        semantic_selected: list[dict[str, Any]] = []
        semantic_candidates: list[dict[str, Any]] = []
        ranked: list[dict[str, Any]] = []
        provider = resolve_embedding_provider(self) if query else None
        if not query:
            logger.debug(
                "[Romantic Memory] semantic recall skipped: empty query; recent channel still evaluated | session=%s | personality=%s",
                session_id,
                personality_id,
            )
        elif provider is None:
            logger.warning(
                "[Romantic Memory] semantic recall skipped: embedding provider unavailable | session=%s | personality=%s",
                session_id,
                personality_id,
            )
        else:
            try:
                vector = await provider.get_embedding(query)
                candidate_limit = max(20, int(self.config.get("recall_top_k", 5) or 5) * 4)
                semantic_candidates = await asyncio.to_thread(
                    self.store.query,
                    vector,
                    session_id,
                    candidate_limit,
                    personality_id,
                )
                ranked = rank_candidates(
                    semantic_candidates,
                    query,
                    float(self.config.get("recall_score_threshold", 0.35) or 0.35),
                    bool(self.config.get("enable_time_decay", True)),
                    float(self.config.get("time_decay_coefficient", 0.01) or 0.01),
                )
                semantic_selected = select_for_injection(
                    ranked,
                    int(self.config.get("recall_top_k", 5) or 5),
                    int(self.config.get("max_inject_chars", 6000) or 6000),
                    -1,
                )
                semantic_before_dedupe = len(semantic_selected)
                semantic_selected = [
                    item for item in semantic_selected if str(item.get("id", "")) not in recent_ids
                ]
                if semantic_selected:
                    semantic_text = format_memory_prompt(semantic_template, semantic_selected)
                    inject_request(
                        req,
                        semantic_text,
                        str(self.config.get("insert_method", "user_prompt")),
                        str(self.config.get("insert_position", "prepend")),
                    )
                    injected_count += len(semantic_selected)
                    selected_log.extend(
                        {
                            "id": item.get("id", ""),
                            "date": item.get("date", ""),
                            "content": truncate_text(str(item.get("content", "")), 240),
                            "final_score": round(float(item.get("final_score", 0.0) or 0.0), 4),
                            "source": "semantic",
                        }
                        for item in semantic_selected
                    )
                    logger.info(
                        "[Romantic Memory] recalled related memories into ProviderRequest | session=%s | personality=%s | semantic_candidates=%s | ranked=%s | selected=%s | deduplicated_recent=%s | method=%s | position=%s\n%s",
                        session_id,
                        personality_id,
                        len(semantic_candidates),
                        len(ranked),
                        len(semantic_selected),
                        semantic_before_dedupe - len(semantic_selected),
                        str(self.config.get("insert_method", "user_prompt")),
                        str(self.config.get("insert_position", "prepend")),
                        semantic_text,
                    )
                else:
                    logger.info(
                        "[Romantic Memory] semantic recall produced no additional memories | session=%s | personality=%s | candidates=%s | ranked=%s | deduplicated_recent=%s",
                        session_id,
                        personality_id,
                        len(semantic_candidates),
                        len(ranked),
                        semantic_before_dedupe - len(semantic_selected),
                    )
            except Exception as exc:
                logger.error(
                    "[Romantic Memory] semantic recall failed: %s | session=%s | personality=%s",
                    exc,
                    session_id,
                    personality_id,
                    exc_info=True,
                )

        recall["candidate_count"] = len(all_records)
        recall["ranked_count"] = len(ranked)
        recall["recent_count"] = len(recent_selected)
        recall["semantic_candidate_count"] = len(semantic_candidates)
        recall["semantic_selected_count"] = len(semantic_selected)
        recall["selected_count"] = injected_count
        recall["selected"] = selected_log
        if injected_count:
            recall.update({"status": "hit", "reason": "memory_injected"})
        else:
            recall.update({"status": "miss", "reason": "no_recent_or_related_memory"})
    async def _schedule_summary(self, session_id: str, personality_id: str | None = None) -> None:
        task = asyncio.create_task(self.summarize_session(session_id, personality_id))
        self.summary_tasks.add(task)
        task.add_done_callback(self.summary_tasks.discard)

    async def summarize_session(self, session_id: str, personality_id: str | None = None) -> bool:
        state, messages = await self.sessions.snapshot(session_id, personality_id)
        if not messages:
            return False
        try:
            summary = await summarize_messages(self, session_id, messages, state.last_system_prompt)
            vector = await embed(self, summary)
            last_timestamp = max(float(item.get("timestamp", time.time())) for item in messages)
            await self._ensure_store()
            await asyncio.to_thread(
                self.store.add,
                summary,
                vector,
                self.sessions.key(session_id),
                last_timestamp,
                None,
                None,
                state.personality_id,
            )
            self.sessions.commit_summary(state, len(messages))
            self.metrics["summaries_success"] += 1
            self.metrics["last_summary_at"] = time.time()
            logger.info(
                "[Romantic Memory] summary saved | session=%s | personality=%s | messages=%s | chars=%s",
                session_id,
                state.personality_id,
                len(messages),
                len(summary),
            )
            return True
        except Exception as exc:
            self.sessions.rollback_summary(state)
            self.metrics["summaries_failed"] += 1
            self.metrics["last_error"] = str(exc)
            logger.error("Romantic memory summary failed: %s", exc, exc_info=True)
            return False

    @filter.command("恋爱记忆", alias={"romantic_memory_save"})
    async def manual_save_memory(self, event: AstrMessageEvent):
        """Manually summarize the current private conversation into long-term memory."""
        if not self._is_private(event):
            yield event.plain_result("恋爱记忆仅支持私聊。")
            return
        session_id = str(event.unified_msg_origin or "").strip()
        if not session_id:
            yield event.plain_result("无法获取当前私聊会话。")
            return
        personality_id = await self._current_personality_id(event)
        state = await self.sessions.get(session_id, personality_id)
        if state.summary_in_progress:
            yield event.plain_result("当前会话正在整理记忆，请稍后再试。")
            return
        if await self.summarize_session(session_id, personality_id):
            state.last_activity = time.time()
            yield event.plain_result("已手动整理并保存当前私聊记忆，轮数和计时器已重置。")
            return
        yield event.plain_result("当前没有可保存的短期对话，或记忆保存失败。")

    async def save_from_tool(self, event: AstrMessageEvent) -> str:
        if not self.config.get("use_tool_memory", False):
            return "恋爱记忆工具当前未启用。"
        if not self._is_private(event):
            return "恋爱记忆仅支持私聊。"
        session_id = str(event.unified_msg_origin)
        personality_id = await self._current_personality_id(event)
        state = await self.sessions.get(session_id, personality_id)
        if await self.summarize_session(session_id, personality_id):
            return "已将当前私聊整理并保存为长期记忆。"
        return "当前没有可保存的短期对话，或记忆保存失败。"

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            if self.config.get("use_tool_memory", False) or not self.config.get("enable_idle_summary", False):
                continue
            threshold = float(self.config.get("trigger_idle_minutes", 60) or 60) * 60
            now = time.time()
            for session_id, state in self.sessions.all_states().items():
                if state.messages and now - state.last_activity >= threshold and not state.summary_in_progress:
                    await self._schedule_summary(state.session_id, state.personality_id)

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        try:
            await self._ensure_store()
            restored = await self.sessions.restore()
            if restored:
                logger.info("[Romantic Memory] restored pending short-term sessions | count=%s", restored)
        except Exception as exc:
            self.metrics["last_error"] = str(exc)
            logger.error("Romantic memory Chroma initialization failed: %s", exc)
        try:
            from .web_api import RomanticMemoryWebApi

            RomanticMemoryWebApi(self).register_routes()
        except Exception as exc:
            logger.error("Romantic memory Web API registration failed: %s", exc, exc_info=True)
        try:
            if self.config.get("use_tool_memory", False):
                self.context.activate_llm_tool(TOOL_NAME)
            else:
                self.context.deactivate_llm_tool(TOOL_NAME)
        except Exception:
            logger.warning("[Romantic Memory] unable to switch LLM memory tool", exc_info=True)
        if self.background_task is None and self.config.get("enable_idle_summary", False) and not self.config.get("use_tool_memory", False):
            self.background_task = asyncio.create_task(self._idle_loop())
        logger.info(
            "[Romantic Memory] loaded | isolation=%s | insert_method=%s | trigger_rounds=%s | idle_summary=%s",
            self.sessions.isolated,
            self.config.get("insert_method", "user_prompt"),
            self.config.get("trigger_rounds", 15),
            self.config.get("enable_idle_summary", False),
        )

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if self._internal_call or not self._is_private(event):
            return
        session_id = str(event.unified_msg_origin)
        personality_id = await self._current_personality_id(event)
        prompt = strip_context_metadata(str(req.prompt or event.message_str or ""), self.config.get("custom_filter_terms", ""))
        if prompt:
            await self.sessions.add_message(session_id, "user", prompt, req.system_prompt, personality_id)
        try:
            await self._recall(event, req, self._effective_session(event), personality_id)
        except Exception as exc:
            self.metrics["last_error"] = str(exc)
            self.metrics["last_recall"].update({"status": "error", "reason": "recall_exception"})
            logger.error("[Romantic Memory] recall failed: %s", exc, exc_info=True)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse) -> None:
        if self._internal_call or not self._is_private(event):
            return
        text = str(getattr(resp, "completion_text", "") or "").strip()
        if not text:
            return
        session_id = str(event.unified_msg_origin)
        personality_id = await self._current_personality_id(event)
        state = await self.sessions.add_message(session_id, "assistant", text, personality_id=personality_id)
        if self.config.get("use_tool_memory", False):
            return
        rounds = int(self.config.get("trigger_rounds", 15) or 15)
        if rounds > 0 and state.rounds >= rounds and not state.summary_in_progress:
            await self._schedule_summary(session_id, personality_id)

    async def terminate(self) -> None:
        if self.background_task and not self.background_task.done():
            self.background_task.cancel()
            try:
                await self.background_task
            except asyncio.CancelledError:
                pass
        for task in list(self.summary_tasks):
            task.cancel()
        self.summary_tasks.clear()
        global ACTIVE_PLUGIN
        if ACTIVE_PLUGIN is self:
            ACTIVE_PLUGIN = None
