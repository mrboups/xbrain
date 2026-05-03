"""Second-opinion agent — fan out the same prompt to Claude AND Grok in parallel.

Read-only agent: NEVER writes to memory. Pure comparison helper for users who
suspect a single-model answer might be biased and want a cross-check.

Time-to-response = max(Claude, Grok), not the sum, because the two calls run
under asyncio.gather. Single-API failures degrade gracefully — the other
provider's response is returned with a note explaining what failed.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

from anthropic import AsyncAnthropic
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI  # used for xAI Grok via OpenAI-compat API

from app.config import settings
from app.graphs.registry import register

CLAUDE_MODEL = "claude-3-5-sonnet-latest"
GROK_MODEL = "grok-2-latest"
XAI_BASE_URL = "https://api.x.ai/v1"

# Naive disagreement-signal keywords — Phase 3 will swap for an LLM-as-judge
DISAGREE_WORDS = ("however", "actually", "wrong", "incorrect", "but", "nope", "instead")


class SecondOpinionState(TypedDict, total=False):
    prompt: str
    claude_response: str
    grok_response: str
    claude_error: str | None
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
    (claude_text, claude_err), (grok_text, grok_err) = await asyncio.gather(
        call_claude(prompt),
        call_grok(prompt),
    )
    return {
        **state,
        "claude_response": claude_text,
        "grok_response": grok_text,
        "claude_error": claude_err,
        "grok_error": grok_err,
    }


def diff_node(state: SecondOpinionState) -> SecondOpinionState:
    """Surface obvious surface-level divergences between the two responses.

    Naive Phase 2 heuristics — length delta + disagreement-keyword count delta.
    Phase 3 will swap for a Claude-as-judge call producing structured diff bullets.
    """
    c, g = state.get("claude_response", ""), state.get("grok_response", "")
    if not c or not g:
        return {**state, "diff_highlights": "_(can't diff — one or both responses missing)_"}

    bullets: list[str] = []
    if abs(len(c) - len(g)) > 200:
        bullets.append(
            f"- Length differs significantly: Claude {len(c)} chars, Grok {len(g)} chars"
        )
    c_lower, g_lower = c.lower(), g.lower()
    c_dis = sum(1 for w in DISAGREE_WORDS if w in c_lower)
    g_dis = sum(1 for w in DISAGREE_WORDS if w in g_lower)
    if abs(c_dis - g_dis) >= 2:
        bullets.append(
            f"- Hedging/contrast words differ: Claude={c_dis}, Grok={g_dis}"
        )
    if not bullets:
        bullets.append("- No obvious surface-level divergences (read both for nuance)")
    return {**state, "diff_highlights": "\n".join(bullets)}


def format_node(state: SecondOpinionState) -> SecondOpinionState:
    parts: list[str] = [f"## Claude ({CLAUDE_MODEL})\n\n"]
    if state.get("claude_error"):
        parts.append(f"_unavailable: {state['claude_error']}_\n")
    else:
        parts.append(state.get("claude_response", "") + "\n")
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
