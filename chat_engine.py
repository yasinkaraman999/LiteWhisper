"""OpenRouter chat completions client for the mini chat window.

Sibling to transcriber/openrouter.py in style, but a separate module rather
than another Transcriber subclass — chat has no local-engine equivalent and
messages carry conversation history plus optional images instead of audio.
"""

import base64
import json

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


def stream(messages, model, api_key, cancel_event=None):
    """messages: [{"role": "user"/"assistant", "text": ..., "image_paths": [...]}]
    in chronological order. Yields the assistant's reply text chunk by
    chunk as they arrive over OpenRouter's SSE stream, standard
    OpenAI-compatible format (`data: {...}` lines, terminated by
    `data: [DONE]`, text in choices[0].delta.content).

    Stops early — closing the connection rather than reading it to
    completion — the moment cancel_event is set, checked between chunks.
    """
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
        json={"model": model, "messages": payload_messages, "stream": True},
        timeout=60,
        stream=True,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        detail = response.text[:200]
        raise RuntimeError(f"OpenRouter returned an error ({response.status_code}): {detail}") from e

    try:
        for raw_line in response.iter_lines(decode_unicode=True):
            if cancel_event is not None and cancel_event.is_set():
                return
            if not raw_line or not raw_line.startswith("data: "):
                continue
            data = raw_line[len("data: "):]
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            content = (choices[0].get("delta") or {}).get("content")
            if content:
                yield content
    finally:
        response.close()
