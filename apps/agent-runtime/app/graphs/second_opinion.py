"""Second-opinion agent — fan out the same prompt to Claude, Opus, and Grok in parallel.

Read-only agent: NEVER writes to memory. Pure comparison helper for users who
suspect a single-model answer might be biased and want a cross-check.

Time-to-response = max(Claude, Opus, Grok), not the sum, because the three calls run
under asyncio.gather. Single-API failures degrade gracefully — the other
providers' responses are returned with a note explaining what failed.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

from anthropic import AsyncAnthropic
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI  # used for xAI Grok via OpenAI-compat API

from app.config import settings
from app.graphs.registry import register

CLAUDE_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-7"
GROK_MODEL = "grok-3"
XAI_BASE_URL = "https://api.x.ai/v1"

# Naive disagreement-signal keywords — Phase 3 will swap for an LLM-as-judge
DISAGREE_WORDS = ("however", "actually", "wrong", "incorrect", "but", "nope", "instead")


class SecondOpinionState(TypedDict, total=False):
    prompt: str
    claude_response: str
    opus_response: str
    grok_response: str
    claude_error: str | None
    opus_error: str | None
    grok_error: str | None
    diff_highlights: str
    final_markdown: str


# === LLM call helpers ===


async def call_claude(prompt: str) -> tuple[str, str | None]:
    """Returns (text, error). Either text is non-empty OR error is non-None."""
    if not settings.ANTHROPIC_API_KEY:
        return "", "ANTHROPIC_API_KEY not configured"
    try:
        c = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        r = await c.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in r.content if b.type == "text"), None
    except Exception as e:  # noqa: BLE001 — degrade gracefully, surface error to caller
        return "", f"{type(e).__name__}: {e}"


async def call_opus(prompt: str) -> tuple[str, str | None]:
    """Returns (text, error). Either text is non-empty OR error is non-None."""
    if not settings.ANTHROPIC_API_KEY:
        return "", "ANTHROPIC_API_KEY not configured"
    try:
        c = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        r = await c.messages.create(
            model=OPUS_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in r.content if b.type == "text"), None
    except Exception as e:  # noqa: BLE001 — degrade gracefully, surface error to caller
        return "", f"{type(e).__name__}: {e}"


async def call_grok(prompt: str) -> tuple[str, str | None]:
    if not settings.XAI_API_KEY:
        return "", "XAI_API_KEY not configured"
    try:
        c = AsyncOpenAI(api_key=settings.XAI_API_KEY, base_url=XAI_BASE_URL)
        r = await c.chat.completions.create(
            model=GROK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        return r.choices[0].message.content or "", None
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}: {e}"


# === Graph nodes ===


async def parallel_call_node(state: SecondOpinionState) -> SecondOpinionState:
    prompt = state.get("prompt", "")
    (claude_text, claude_err), (opus_text, opus_err), (grok_text, grok_err) = await asyncio.gather(
        call_claude(prompt),
        call_opus(prompt),
        call_grok(prompt),
    )
    return {
        **state,
        "claude_response": claude_text,
        "opus_response": opus_text,
        "grok_response": grok_text,
        "claude_error": claude_err,
        "opus_error": opus_err,
        "grok_error": grok_err,
    }


def diff_node(state: SecondOpinionState) -> SecondOpinionState:
    """Surface obvious surface-level divergences across Claude, Opus, and Grok.

    Naive heuristics — length deltas + disagreement-keyword counts across the trio.
    Phase 3 will swap for an LLM-as-judge call producing structured diff bullets.
    """
    c = state.get("claude_response", "")
    o = state.get("opus_response", "")
    g = state.get("grok_response", "")
    present = {"Claude": c, "Opus": o, "Grok": g}
    have = {k: v for k, v in present.items() if v}
    if len(have) < 2:
        return {**state, "diff_highlights": "_(can't diff — fewer than two responses available)_"}

    bullets: list[str] = []
    lengths = {k: len(v) for k, v in have.items()}
    if max(lengths.values()) - min(lengths.values()) > 200:
        parts = ", ".join(f"{k} {n} chars" for k, n in lengths.items())
        bullets.append(f"- Length differs significantly: {parts}")

    dis_counts = {
        k: sum(1 for w in DISAGREE_WORDS if w in v.lower()) for k, v in have.items()
    }
    if max(dis_counts.values()) - min(dis_counts.values()) >= 2:
        parts = ", ".join(f"{k}={n}" for k, n in dis_counts.items())
        bullets.append(f"- Hedging/contrast words differ: {parts}")

    if not bullets:
        bullets.append("- No obvious surface-level divergences (read all for nuance)")
    return {**state, "diff_highlights": "\n".join(bullets)}


def format_node(state: SecondOpinionState) -> SecondOpinionState:
    parts: list[str] = [f"## Claude ({CLAUDE_MODEL})\n\n"]
    if state.get("claude_error"):
        parts.append(f"_unavailable: {state['claude_error']}_\n")
    else:
        parts.append(state.get("claude_response", "") + "\n")
    parts.append(f"\n## Opus ({OPUS_MODEL})\n\n")
    if state.get("opus_error"):
        parts.append(f"_unavailable: {state['opus_error']}_\n")
    else:
        parts.append(state.get("opus_response", "") + "\n")
    parts.append(f"\n## Grok ({GROK_MODEL})\n\n")
    if state.get("grok_error"):
        parts.append(f"_unavailable: {state['grok_error']}_\n")
    else:
        parts.append(state.get("grok_response", "") + "\n")
    parts.append("\n## Diff highlights\n\n")
    parts.append(state.get("diff_highlights", ""))
    return {**state, "final_markdown": "".join(parts)}


@register("second-opinion")
def make_graph(checkpointer):
    g = StateGraph(SecondOpinionState)
    g.add_node("parallel", parallel_call_node)
    g.add_node("diff", diff_node)
    g.add_node("format", format_node)
    g.add_edge(START, "parallel")
    g.add_edge("parallel", "diff")
    g.add_edge("diff", "format")
    g.add_edge("format", END)
    return g.compile(checkpointer=checkpointer)
