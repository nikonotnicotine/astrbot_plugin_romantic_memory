from __future__ import annotations

import time

from astrbot_plugin_romantic_memory.retrieval import (
    extract_keywords,
    filter_recent_memories,
    inject_request,
    rank_candidates,
    select_for_injection,
)


def test_extract_keywords_supports_chinese_and_latin_terms():
    terms = extract_keywords("记得那只流浪狗和Charlie吗 2025")
    assert "流浪" in terms
    assert "charlie" in terms
    assert "2025" in terms


def test_hybrid_rank_applies_decay_and_threshold():
    now = time.time()
    candidates = [
        {"id": "old", "content": "流浪狗", "score": 0.9, "timestamp": now - 100 * 86400},
        {"id": "new", "content": "流浪狗", "score": 0.8, "timestamp": now},
    ]
    ranked = rank_candidates(candidates, "流浪狗", 0.35, True, 0.01, now)
    assert [item["id"] for item in ranked] == ["new", "old"]
    assert ranked[0]["final_score"] >= 0.35


def test_character_budget_selects_by_score_and_orders_by_date():
    records = [
        {"id": "old", "content": "旧记忆", "date": "2024-01-01", "timestamp": 1, "final_score": 0.9},
        {"id": "new", "content": "新记忆", "date": "2025-01-01", "timestamp": 2, "final_score": 0.8},
    ]
    selected = select_for_injection(records, 2, 20, -1)
    assert [item["id"] for item in selected] == ["new", "old"]


def test_injection_modes_modify_provider_request():
    class Request:
        prompt = "hello"
        system_prompt = "system"
        contexts = []

    request = Request()
    inject_request(request, "memory", "user_prompt", "prepend")
    assert request.prompt.startswith("memory")
    request = Request()
    inject_request(request, "memory", "insert_system_prompt", "append")
    assert request.contexts[-1]["content"] == "memory"



def test_recent_memory_filter_ignores_scores_and_budgets():
    now = 1_000_000.0
    records = [
        {"id": "recent-long", "content": "x" * 200, "timestamp": now - 10},
        {"id": "recent-low-score", "content": "recent", "timestamp": now - 2 * 86400},
        {"id": "old", "content": "old", "timestamp": now - 4 * 86400},
    ]
    selected = filter_recent_memories(records, 3, now)
    assert [item["id"] for item in selected] == ["recent-long", "recent-low-score"]
    assert [item["id"] for item in filter_recent_memories(records, -1, now)] == [
        "recent-long",
        "recent-low-score",
        "old",
    ]

def test_time_decay_only_orders_relevant_memories():
    now = 1_000_000.0
    old_match = {
        "id": "old-match",
        "content": "TikTok new ID is Lucien ATM",
        "timestamp": now - 78 * 86400,
        "score": 0.8,
    }
    ranked = rank_candidates([old_match], "TikTok ID", 0.35, True, 0.01, now)
    assert [item["id"] for item in ranked] == ["old-match"]
    assert ranked[0]["base_score"] >= 0.35
    assert ranked[0]["final_score"] < 0.35


def test_recent_prompt_uses_recent_memory_label():
    from astrbot_plugin_romantic_memory.retrieval import RECENT_MEMORY_PROMPT

    rendered = RECENT_MEMORY_PROMPT.replace("{memory_text}", "RECENT")
    assert rendered.startswith("【系统提示】以下是你近期的记忆：")

def test_recent_memories_are_grouped_by_date():
    from astrbot_plugin_romantic_memory.retrieval import format_recent_memory_prompt

    rendered = format_recent_memory_prompt(
        [
            {"date": "2026-07-27", "content": "first"},
            {"date": "2026-07-27", "content": "second"},
            {"date": "2026-07-26", "content": "third"},
        ]
    )
    assert rendered == "【系统提示】以下是你近期的记忆：- 2026-07-27 | first\nsecond\n2026-07-26 | third"