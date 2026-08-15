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
