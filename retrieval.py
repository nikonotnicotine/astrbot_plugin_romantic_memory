"""Keyword extraction, time-weighted hybrid retrieval and prompt injection."""

from __future__ import annotations

import re
import time
from typing import Any

RECENT_MEMORY_PROMPT = "【系统提示】以下是你近期的记忆：{memory_text}"


def truncate_text(text: str, limit: int) -> str:
    """Limit text length before sending it to an embedding provider."""
    if limit <= 0:
        return ""
    return text[:limit]


def extract_keywords(text: str) -> list[str]:
    """Extract useful Chinese, Latin and numeric query terms."""
    terms: list[str] = []
    normalized = text.lower()
    terms.extend(re.findall(r"[a-z][a-z0-9_-]{1,31}", normalized))
    terms.extend(re.findall(r"\d+(?:\.\d+)?", normalized))
    cjk = re.findall(r"[\u4e00-\u9fff]+", normalized)
    for block in cjk:
        if len(block) <= 2:
            terms.append(block)
        else:
            terms.extend(block[i : i + 2] for i in range(len(block) - 1))
    return list(dict.fromkeys(term for term in terms if term))


def semantic_score_from_distance(distance: float) -> float:
    """Convert a Chroma distance into a bounded similarity score."""
    if distance < 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 / (1.0 + distance)))


def keyword_score(query_terms: list[str], content: str, metadata_keywords: str = "") -> float:
    """Calculate the fraction of query terms present in a memory."""
    if not query_terms:
        return 0.0
    haystack = f"{content.lower()} {metadata_keywords.lower()}"
    hits = sum(1 for term in query_terms if term in haystack)
    return hits / len(query_terms)


def rank_candidates(
    candidates: list[dict[str, Any]],
    query: str,
    threshold: float,
    enable_time_decay: bool,
    decay_coefficient: float,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Apply hybrid scoring, time decay and threshold filtering."""
    now = now or time.time()
    terms = extract_keywords(query)
    ranked: list[dict[str, Any]] = []
    for item in candidates:
        semantic = float(item.get("score", item.get("_score", 0.0)) or 0.0)
        kw = keyword_score(terms, str(item.get("content", "")), str(item.get("keywords", "")))
        base = semantic * 0.7 + kw * 0.3
        timestamp = float(item.get("timestamp", item.get("create_time", now)) or now)
        age_days = max(0.0, (now - timestamp) / 86400.0)
        final = base - age_days * decay_coefficient if enable_time_decay else base
        item = dict(item)
        item.update(
            {
                "semantic_score": semantic,
                "keyword_score": kw,
                "base_score": base,
                "age_days": age_days,
                "final_score": final,
            }
        )
        # Relevance determines eligibility; time decay only changes ordering.
        # An old but clearly matching memory must not disappear solely because of age.
        if base >= threshold:
            ranked.append(item)
    return sorted(ranked, key=lambda item: item["final_score"], reverse=True)


def select_for_injection(
    ranked: list[dict[str, Any]],
    top_k: int,
    max_chars: int,
    context_keep_days: int,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Select by score under the character budget, then order by date.

    context_keep_days < 0 keeps all candidates; otherwise only memories from
    the latest configured number of days are retained.
    """
    candidates = ranked[: max(0, top_k)]
    if context_keep_days >= 0:
        current_time = time.time() if now is None else now
        cutoff = current_time - context_keep_days * 86400
        candidates = [
            item
            for item in candidates
            if float(item.get("timestamp", 0) or 0) >= cutoff
        ]
    selected: list[dict[str, Any]] = []
    used = 0
    for item in candidates:
        line = f"{item.get('date', '')} | {item.get('content', '')}".strip()
        extra = len(line) + (1 if selected else 0)
        if max_chars >= 0 and used + extra > max_chars:
            continue
        selected.append(item)
        used += extra
    return sorted(selected, key=lambda item: float(item.get("timestamp", 0) or 0), reverse=True)


def filter_recent_memories(
    records: list[dict[str, Any]],
    keep_days: int,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return every memory inside the configured rolling time window."""
    if keep_days < 0:
        return sorted(records, key=lambda item: float(item.get("timestamp", 0) or 0), reverse=True)
    current_time = time.time() if now is None else now
    cutoff = current_time - keep_days * 86400
    selected = [
        item
        for item in records
        if float(item.get("timestamp", 0) or 0) >= cutoff
    ]
    return sorted(selected, key=lambda item: float(item.get("timestamp", 0) or 0), reverse=True)


def format_memory_prompt(template: str, memories: list[dict[str, Any]]) -> str:
    """Render the configured recall prompt."""
    text = "\n".join(f"- {item.get('date', 'unknown date')} | {item.get('content', '')}" for item in memories)
    return template.replace("{memory_text}", text)


def format_recent_memory_prompt(memories: list[dict[str, Any]]) -> str:
    """Render recent memories grouped by date without repeating the date."""
    lines: list[str] = []
    previous_date: str | None = None
    for item in memories:
        date = str(item.get("date", "unknown date") or "unknown date")
        content = str(item.get("content", ""))
        if date != previous_date:
            prefix = "- " if not lines else ""
            lines.append(f"{prefix}{date} | {content}")
            previous_date = date
        else:
            lines.append(content)
    return RECENT_MEMORY_PROMPT.replace("{memory_text}", "\n".join(lines))

def inject_request(req: Any, recall_text: str, method: str, position: str) -> None:
    """Inject recall text into a ProviderRequest in the selected location."""
    if not recall_text:
        return
    method = method if method in {"user_prompt", "system_prompt", "insert_system_prompt"} else "user_prompt"
    position = position if position in {"prepend", "append"} else "prepend"
    if method == "user_prompt":
        current = req.prompt or ""
        req.prompt = recall_text + "\n\n" + current if position == "prepend" else current + "\n\n" + recall_text
    elif method == "system_prompt":
        current = req.system_prompt or ""
        req.system_prompt = recall_text + "\n\n" + current if position == "prepend" else current + "\n\n" + recall_text
    else:
        message = {"role": "system", "content": recall_text}
        if position == "prepend":
            req.contexts.insert(0, message)
        else:
            req.contexts.append(message)
