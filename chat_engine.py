"""OpenRouter chat completions client for the mini chat window.

Sibling to transcriber/openrouter.py in style, but a separate module rather
than another Transcriber subclass — chat has no local-engine equivalent and
messages carry conversation history plus optional images instead of audio.
"""

import base64

import requests

CHAT_API_URL = "https://openrouter.ai/api/v1/chat/completions"

_MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def _mime_for(path):
    ext = str(path).rsplit(".", 1)[-1].lower()
    return _MIME_TYPES.get(ext, "image/png")


def _content(text, image_paths):
    """A plain string for text-only messages, or an OpenAI-style multimodal
    content array once images are attached."""
    if not image_paths:
        return text
    parts = []
    if text:
        parts.append({"type": "text", "text": text})
    for path in image_paths:
        with open(path, "rb") as f:
            data_b64 = base64.b64encode(f.read()).decode("utf-8")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{_mime_for(path)};base64,{data_b64}"},
        })
    return parts


def send(messages, model, api_key):
    """messages: [{"role": "user"/"assistant", "text": ..., "image_paths": [...]}]
    in chronological order. Returns the assistant's reply text."""
    if not api_key:
        raise ValueError("OpenRouter API key not set")
    if not model:
        raise ValueError("No chat model selected")

    payload_messages = [
        {"role": m["role"], "content": _content(m.get("text", ""), m.get("image_paths"))}
        for m in messages
    ]

    response = requests.post(
        CHAT_API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": payload_messages},
        timeout=60,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        detail = response.text[:200]
        raise RuntimeError(f"OpenRouter returned an error ({response.status_code}): {detail}") from e

    body = response.json()
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"Unexpected OpenRouter response: {body}")
    return choices[0]["message"]["content"]
