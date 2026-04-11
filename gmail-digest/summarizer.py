import os

import anthropic

SYSTEM_PROMPT = """\
You are an email digest assistant. Summarize the following emails into a concise \
daily briefing for the user. Group emails by topic or importance. Use markdown formatting. \
For each email or group, mention the sender and subject. Highlight anything that looks \
urgent or requires action. Keep the digest brief and scannable."""


def summarize(emails: list[dict]) -> str:
    """Summarize a list of email dicts into a markdown digest using Claude."""
    # Format emails into a readable block
    parts = []
    for i, e in enumerate(emails, 1):
        parts.append(
            f"--- Email {i} ---\n"
            f"From: {e['sender']}\n"
            f"Subject: {e['subject']}\n"
            f"Date: {e['date']}\n"
            f"Body:\n{e['body'] or e['snippet']}\n"
        )
    emails_text = "\n".join(parts)

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Here are {len(emails)} new emails:\n\n{emails_text}"}
        ],
    )
    return message.content[0].text
