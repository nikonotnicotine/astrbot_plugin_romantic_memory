from __future__ import annotations

import asyncio

from astrbot_plugin_romantic_memory.session_manager import SessionManager


def test_sessions_are_isolated_and_summary_commit_is_atomic():
    async def run():
        manager = SessionManager(True)
        await manager.add_message("a", "user", "A")
        await manager.add_message("b", "user", "B")
        assert [item["content"] for item in await manager.pending("a")] == ["A"]
        state, snapshot = await manager.snapshot("a")
        assert snapshot[0]["content"] == "A"
        manager.rollback_summary(state)
        assert await manager.pending("a")
        state, snapshot = await manager.snapshot("a")
        manager.commit_summary(state, len(snapshot))
        assert await manager.pending("a") == []

    asyncio.run(run())


def test_shared_mode_uses_one_buffer():
    async def run():
        manager = SessionManager(False)
        await manager.add_message("a", "user", "A")
        await manager.add_message("b", "user", "B")
        assert len(await manager.pending("a")) == 2

    asyncio.run(run())


def test_short_term_messages_can_be_edited_and_deleted(tmp_path):
    async def run():
        manager = SessionManager(True, tmp_path)
        state = await manager.add_message(
            "session", "user", "before", personality_id="persona"
        )
        message_id = state.messages[0]["id"]

        updated = await manager.update_message(
            "session", "persona", message_id, "after"
        )
        assert updated is not None
        assert updated["content"] == "after"
        assert [
            item["content"] for item in await manager.pending("session", "persona")
        ] == ["after"]

        assert await manager.delete_message("session", "persona", message_id)
        assert await manager.pending("session", "persona") == []

    asyncio.run(run())
