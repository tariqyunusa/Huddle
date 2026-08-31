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
    "speaker individually."
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
                "content": "Generate a short, descriptive title (max 6 words, no quotes, no punctuation at the end) summarizing the topic of this message. Reply with only the title, nothing else.",
            },
            {"role": "user", "content": first_message},
        ],
    )
    title = response.choices[0].message.content
    return title.strip() if title else "Untitled session"