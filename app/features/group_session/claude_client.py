"""
Groq integration for group reasoning sessions — Brick 5 (swapped from Anthropic for free testing).
"""
import os
from typing import List

from openai import AsyncOpenAI

from .models import GroupMessage

client = AsyncOpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)

SYSTEM_PROMPT = (
    "You are participating in a group reasoning session with multiple human "
    "participants, each prefixed by name (e.g. '[Alice]: ...'). Address the "
    "group's question directly; you don't need to greet or acknowledge each "
    "speaker individually. Use markdown tables when comparing multiple items "
    "or presenting structured data. Use Mermaid diagrams (in a ```mermaid code "
    "block) when a flowchart, sequence diagram, or process visualization would "
    "clarify your answer."
)


def build_transcript(messages: List[GroupMessage]) -> List[dict]:
    """
    Flatten the group history into a single messages array.
    Multiple human participants get folded into 'user' turns, each line
    prefixed with who said it. Consecutive user turns get merged.
    """
    turns: List[dict] = []
    for m in messages:
        if m.role == "user":
            line = f"[{m.author_name}]: {m.content}"
            if turns and turns[-1]["role"] == "user":
                turns[-1]["content"] += "\n" + line
            else:
                turns.append({"role": "user", "content": line})
        else:
            turns.append({"role": "assistant", "content": m.content})
    return turns


async def call_claude(messages: List[dict]) -> str:
    response = await client.chat.completions.create(
        model="openai/gpt-oss-120b",
        max_tokens=2000,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
    )
    return response.choices[0].message.content

async def generate_title(first_message: str) -> str:
    response = await client.chat.completions.create(
        model="openai/gpt-oss-120b",
        max_tokens=20,
        extra_body={"include_reasoning": False},
        messages=[
            {
                "role": "system",
                "content": (
                    "You generate short chat titles. Read the user's message and "
                    "produce a 3-6 word title describing its specific topic. "
                    "Do not use generic phrases like 'New session' or 'Untitled session'. "
                    "Reply with ONLY the title text, no quotes, no punctuation at the end.\n\n"
                    "Example:\nMessage: 'what is innovation'\nTitle: Understanding Innovation"
                ),
            },
            {"role": "user", "content": first_message},
        ],
    )
    title = response.choices[0].message.content
    title = title.strip() if title else ""
    if not title or title.lower() in ("untitled session", "new session"):
        return first_message[:40] + ("..." if len(first_message) > 40 else "")
    return title