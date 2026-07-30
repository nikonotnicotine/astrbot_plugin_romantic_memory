from __future__ import annotations

from astrbot_plugin_romantic_memory.memory_store import MemoryStore


def test_text_and_json_import_parsing():
    text_records = MemoryStore.parse_import("2025-03-16 | 记住流浪狗\n普通记忆", "txt", "s1")
    assert text_records[0]["date"] == "2025-03-16"
    assert text_records[0]["session_id"] == "s1"
    assert len(text_records) == 2
    json_records = MemoryStore.parse_import('{"memories":[{"content":"x"}]}', "json")
    assert json_records == [{"content": "x"}]

