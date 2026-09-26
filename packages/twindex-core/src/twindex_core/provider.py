from __future__ import annotations

import base64
import ipaddress
import os
from io import BytesIO
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError
from PIL import Image

from .engine import ModelOutputError, VisionUnavailable


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        profile: Literal["ollama", "cloud", "custom"] = "ollama",
        model: str | None = None,
        embedding_model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        allow_cloud_sources: bool = False,
        vision: bool | None = None,
        request_timeout: float | None = None,
        client: httpx.Client | None = None,
    ):
        self.profile = profile
        self.model = model or (
            "gpt-5.6-luna" if profile == "cloud" else "qwen3-vl:4b-instruct"
        )
        self.embedding_model = embedding_model or (
            "text-embedding-3-small" if profile == "cloud" else "qwen3-embedding:0.6b"
        )
        self.base_url = (
            base_url
            or (
                "https://api.openai.com"
                if profile == "cloud"
                else "http://127.0.0.1:11434"
            )
        ).rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.api_key = api_key or (
            os.environ.get("OPENAI_API_KEY") if profile == "cloud" else None
        )
        self.allow_cloud_sources = allow_cloud_sources
        self._vision = (
            vision if profile == "custom" else (True if profile == "cloud" else None)
        )
        self._vision_checked = profile != "custom"
        timeout = request_timeout or (90 if profile == "cloud" else 180)
        self.client = client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10),
            trust_env=False,
        )
        self._owns_client = client is None
        self._completion_metadata: dict = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> OpenAICompatibleProvider:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = self.client.post(path, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ModelOutputError("Provider returned a non-object response")
        return result

    def _cloud_guard(self) -> None:
        parsed = urlsplit(self.base_url)
        try:
            local = (
                parsed.hostname == "localhost"
                or ipaddress.ip_address(parsed.hostname or "").is_loopback
            )
        except ValueError:
            local = False
        if not local and not self.allow_cloud_sources:
            raise PermissionError("Remote source transfer requires explicit approval")
        if not local and parsed.scheme != "https":
            raise ValueError("Remote model endpoint must use HTTPS")
        if self.profile == "cloud" and not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for the cloud profile")

    def _completion(
        self, messages: list[dict[str, Any]], *, response_format: dict | None = None
    ) -> str:
        self._cloud_guard()
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.profile == "ollama":
            payload.update(
                {"temperature": 0, "max_tokens": 2048, "reasoning_effort": "none"}
            )
        if response_format:
            payload["response_format"] = response_format
        result = self._post("/v1/chat/completions", payload)
        self._completion_metadata = {}
        try:
            choice = result["choices"][0]
            finish = choice.get("finish_reason")
            self._completion_metadata["finish_reason"] = (
                finish
                if finish in {"stop", "length", "content_filter", "tool_calls"}
                else "unknown"
            )
            usage = result.get("usage", {})
            if isinstance(usage, dict):
                self._completion_metadata["usage"] = {
                    key: usage[key]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if isinstance(usage.get(key), int)
                }
            message = choice["message"]
            if message.get("refusal") or finish == "content_filter":
                raise ModelOutputError("Provider refused the request")
            content = message["content"]
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ModelOutputError("Provider returned no message content") from exc
        if not isinstance(content, str):
            raise ModelOutputError("Provider message is not text")
        self._completion_metadata["output_characters"] = len(content)
        if finish == "length":
            raise ModelOutputError(
                "Provider output was truncated (finish_reason=length)"
            )
        return content

    def structured(self, system: str, user: str, schema: type[BaseModel]) -> dict:
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
            },
        }
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        attempts = []
        fields: set[str] = set()

        def collect_fields(node):
            if isinstance(node, dict):
                fields.update(node.get("properties", {}).keys())
                for value in node.values():
                    collect_fields(value)
            elif isinstance(node, list):
                for value in node:
                    collect_fields(value)

        collect_fields(response_format["json_schema"]["schema"])
        for _ in range(2):
            self._completion_metadata = {}
            try:
                raw = self._completion(messages, response_format=response_format)
            except ModelOutputError as exc:
                attempts.append(
                    {
                        **self._completion_metadata,
                        "errors": [{"field": "$", "code": "completion_invalid"}],
                    }
                )
                raise ModelOutputError(
                    str(exc),
                    diagnostics={"schema": schema.__name__, "attempts": attempts},
                ) from exc
            try:
                parsed = schema.model_validate_json(raw)
                return parsed.model_dump(mode="json")
            except ValidationError as exc:
                # Do not retain input, provider text, exception messages or validation ctx.
                errors = [
                    {
                        "field": ".".join(
                            str(part)
                            if isinstance(part, int) or part in fields
                            else "<field>"
                            for part in error["loc"]
                        )
                        or "$",
                        "code": error["type"],
                    }
                    for error in exc.errors(
                        include_input=False, include_context=False, include_url=False
                    )[:10]
                ]
                attempts.append({**self._completion_metadata, "errors": errors})
                messages.append({"role": "assistant", "content": raw[:2000]})
                messages.append(
                    {
                        "role": "user",
                        "content": "Return only valid JSON matching the schema. Fix these fields: "
                        + str(errors)
                        + ". An answer with insufficient_evidence=false requires nonempty text and evidence_refs.",
                    }
                )
        raise ModelOutputError(
            "Provider did not return valid structured output",
            diagnostics={"schema": schema.__name__, "attempts": attempts},
        )

    def text(self, system: str, user: str) -> str:
        return self._completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )

    def require_vision(self) -> None:
        if self._vision is None and self.profile == "ollama":
            result = self._post("/api/show", {"model": self.model})
            capabilities = result.get("capabilities", [])
            self._vision = isinstance(capabilities, list) and "vision" in capabilities
        if not self._vision:
            raise VisionUnavailable(
                f"Model {self.model} does not advertise vision support"
            )
        if not self._vision_checked:
            buffer = BytesIO()
            Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            try:
                probe_reply = self._completion(
                    [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Reply OK if you can inspect this test image.",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{encoded}"
                                    },
                                },
                            ],
                        }
                    ]
                )
                if not probe_reply.strip():
                    raise ValueError("Empty vision probe response")
            except (httpx.HTTPError, ModelOutputError, ValueError) as exc:
                raise VisionUnavailable(
                    f"Model {self.model} failed a vision probe"
                ) from exc
            self._vision_checked = True

    def describe_image(self, image: bytes, *, mime_type: str) -> str:
        self.require_vision()
        encoded = base64.b64encode(image).decode("ascii")
        content = [
            {
                "type": "text",
                "text": "Transcribe and describe only what is visible. State uncertainty.",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            },
        ]
        return self._completion([{"role": "user", "content": content}])

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._cloud_guard()
        if not texts:
            return []
        result = self._post(
            "/v1/embeddings", {"model": self.embedding_model, "input": texts}
        )
        try:
            data = sorted(result["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in data]
        except (KeyError, TypeError) as exc:
            raise ModelOutputError("Invalid embedding response") from exc
        if len(vectors) != len(texts) or any(
            not isinstance(vec, list) or not vec for vec in vectors
        ):
            raise ModelOutputError("Embedding count or shape mismatch")
        return vectors

    def diagnose(self) -> dict[str, Any]:
        self._cloud_guard()
        if not self._completion([{"role": "user", "content": "Reply OK"}]).strip():
            raise ModelOutputError("Chat model returned an empty diagnostic reply")
        vision = False
        try:
            self.require_vision()
            vision = True
        except VisionUnavailable:
            pass
        embeddings = False
        try:
            self.embed(["capability test"])
            embeddings = True
        except (httpx.HTTPError, ModelOutputError):
            pass
        return {
            "profile": self.profile,
            "model": self.model,
            "embedding_model": self.embedding_model,
            "chat": True,
            "vision": vision,
            "embeddings": embeddings,
            "operations": {
                "text_proposals": True,
                "image_proposals": vision,
                "scanned_pdf_proposals": vision,
                "fts_search": True,
                "semantic_search": embeddings,
            },
        }
