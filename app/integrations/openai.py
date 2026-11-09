"""Single no-retry OpenAI Responses adapter with fixed endpoint and bounded output."""

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from app.integrations.http import NoRedirect


class ModelUnavailable(Exception):
    """A model call did not provide a complete usable result; billing may be uncertain."""


def create_response(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call the fixed provider once; never follow redirects or retry uncertain requests."""
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=45) as response:
            raw = response.read(256_001)
        if len(raw) > 256_000:
            raise ModelUnavailable("Provider response exceeds the size limit")
        parsed: object = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ModelUnavailable("Provider response was not an object")
        return parsed
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise ModelUnavailable("Provider unavailable; review the reserved call") from error


def _batch_request(
    api_key: str, path: str, data: bytes | None = None, content_type: str = "application/json"
) -> bytes:
    """Call only fixed batch/files paths once; never follow provider redirects."""
    import re

    if not re.fullmatch(r"/(?:files|batches)(?:/[a-zA-Z0-9_-]+(?:/content)?)?", path):
        raise ModelUnavailable("Invalid provider resource")
    request = Request(
        "https://api.openai.com/v1" + path,
        data=data,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": content_type},
        method="POST" if data is not None else "GET",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=45) as result:
            raw = result.read(256_001)
        if len(raw) > 256_000:
            raise ModelUnavailable("Batch response exceeds limit")
        return bytes(raw)
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise ModelUnavailable("Batch request unavailable; reservation retained") from error


def submit_batch(api_key: str, payload: dict[str, Any], custom_id: str) -> str:
    """Upload a one-request JSONL batch; separate jobs can use separate models."""
    import uuid

    boundary = "webix" + uuid.uuid4().hex
    line = (
        json.dumps(
            {"custom_id": custom_id, "method": "POST", "url": "/v1/responses", "body": payload}
        )
        + "\n"
    )
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nbatch\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="input.jsonl"\r\n'
        f"Content-Type: application/jsonl\r\n\r\n{line}\r\n--{boundary}--\r\n"
    ).encode()
    try:
        uploaded = json.loads(
            _batch_request(api_key, "/files", body, "multipart/form-data; boundary=" + boundary)
        )
        created = json.loads(
            _batch_request(
                api_key,
                "/batches",
                json.dumps(
                    {
                        "input_file_id": uploaded["id"],
                        "endpoint": "/v1/responses",
                        "completion_window": "24h",
                    }
                ).encode(),
            )
        )
        batch_id = created["id"]
        if not isinstance(batch_id, str) or not batch_id.startswith("batch_"):
            raise ValueError("Invalid batch identity")
        return batch_id
    except (KeyError, TypeError, ValueError) as error:
        raise ModelUnavailable("Invalid batch submission response") from error


def batch_result(api_key: str, batch_id: str, custom_id: str) -> dict[str, Any] | None:
    """Match output by custom_id, never by line order; terminal failures stay reviewable."""
    try:
        status = json.loads(_batch_request(api_key, "/batches/" + batch_id))
        if status["status"] in {"validating", "in_progress", "finalizing", "cancelling"}:
            return None
        if status["status"] != "completed" or not status.get("output_file_id"):
            raise ModelUnavailable("Batch failed, expired or was cancelled")
        raw = _batch_request(api_key, "/files/" + status["output_file_id"] + "/content")
        matches = [json.loads(line) for line in raw.splitlines() if line.strip()]
        matches = [item for item in matches if item.get("custom_id") == custom_id]
        if len(matches) != 1 or matches[0].get("error"):
            raise ModelUnavailable("Missing, duplicated or failed batch result")
        result = matches[0]["response"]
        if result["status_code"] != 200 or not isinstance(result["body"], dict):
            raise ModelUnavailable("Batch request was rejected")
        return dict(result["body"])
    except (KeyError, TypeError, ValueError) as error:
        raise ModelUnavailable("Invalid batch result") from error
