import requests

MODELS_URL = "https://openrouter.ai/api/v1/models"


def fetch_cloud_models(api_key):
    """Returns a list of {"id": ..., "name": ...} for OpenRouter models that
    support audio transcription."""
    response = requests.get(
        MODELS_URL,
        params={"output_modalities": "transcription"},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json().get("data", [])
    return [{"id": m["id"], "name": m.get("name", m["id"])} for m in data]


def fetch_chat_models(api_key):
    """Returns a list of {"id": ..., "name": ..., "supports_images": ...} for
    OpenRouter's full chat-completion model catalogue."""
    response = requests.get(
        MODELS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json().get("data", [])
    result = []
    for m in data:
        architecture = m.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        result.append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "supports_images": "image" in input_modalities,
        })
    return result
