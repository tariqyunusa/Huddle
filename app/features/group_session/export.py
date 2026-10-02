import io

import markdown as md
from docx import Document as DocxDocument
from htmldocx import HtmlToDocx
from weasyprint import HTML

from .talon import client, SYSTEM_PROMPT  # reuse the same Groq client


async def generate_document_markdown(transcript: list[dict], instructions: str) -> tuple[str, int]:
    prompt = (
        "Based on the group's conversation so far, write a clear, well-structured "
        "document in markdown. Use headers, bullet points, and bold text where it "
        "aids readability. Do not include meta-commentary about the task — output "
        f"only the document itself.\n\nSpecific instructions: {instructions}"
    )
    response = await client.chat.completions.create(
        model="openai/gpt-oss-120b",
        max_tokens=3000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *transcript,
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    tokens_used = response.usage.total_tokens if response.usage else 0
    return content, tokens_used


def markdown_to_docx(markdown_text: str) -> io.BytesIO:
    html = md.markdown(markdown_text, extensions=["tables", "fenced_code"])
    doc = DocxDocument()
    HtmlToDocx().add_html_to_document(html, doc)
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def markdown_to_pdf(markdown_text: str) -> io.BytesIO:
    html = md.markdown(markdown_text, extensions=["tables", "fenced_code"])
    styled_html = f"""
    <html><head><style>
        body {{ font-family: sans-serif; line-height: 1.5; margin: 2cm; }}
        h1, h2, h3 {{ color: #222; }}
        table {{ border-collapse: collapse; width: 100%; }}
        td, th {{ border: 1px solid #ccc; padding: 6px; }}
    </style></head><body>{html}</body></html>
    """
    buffer = io.BytesIO()
    HTML(string=styled_html).write_pdf(buffer)
    buffer.seek(0)
    return buffer