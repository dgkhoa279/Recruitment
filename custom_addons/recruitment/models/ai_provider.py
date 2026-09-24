# -*- coding: utf-8 -*-
"""Provider adapters for cloud and local recruitment AI workloads."""

import json
import ipaddress
import logging
from urllib.parse import urlparse

import requests


_logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """A safe, user-facing provider failure."""


class BaseProvider:
    provider_name = "base"

    def __init__(self, config):
        self.config = config
        self.model = config["model"]
        self.timeout = int(config.get("timeout", 60))

    def _post(self, url, payload, headers=None, timeout=None):
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers or {"Content-Type": "application/json"},
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", "N/A")
            _logger.warning("AI provider %s HTTP error (%s): %s", self.provider_name, status, exc)
            raise ProviderError(
                f"Không thể kết nối AI provider '{self.provider_name}' (HTTP {status})."
            ) from exc
        except ValueError as exc:
            raise ProviderError(
                f"AI provider '{self.provider_name}' trả về dữ liệu không hợp lệ."
            ) from exc

    def structured_json(self, prompt, system=None):
        raise NotImplementedError

    def generate_text(self, prompt, system=None, max_tokens=1200):
        raise NotImplementedError

    def embed(self, text, model=None):
        raise NotImplementedError


class OllamaProvider(BaseProvider):
    provider_name = "ollama"

    def __init__(self, config):
        super().__init__(config)
        self.base_url = config.get("base_url", "http://localhost:11434").rstrip("/")

    def _chat(self, messages, json_mode=False, max_tokens=1200):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"
        data = self._post(f"{self.base_url}/api/chat", payload, timeout=90)
        return data.get("message") or {}

    def structured_json(self, prompt, system=None):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        message = self._chat(messages, json_mode=True, max_tokens=1600)
        try:
            return json.loads(message.get("content") or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError("Ollama không trả về JSON hợp lệ.") from exc

    def generate_text(self, prompt, system=None, max_tokens=1200):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._chat(messages, max_tokens=max_tokens).get("content") or ""

    def embed(self, text, model=None):
        embedding_model = model or self.config.get("embedding_model")
        if not embedding_model:
            raise ProviderError("Chưa cấu hình Ollama embedding model.")
        data = self._post(
            f"{self.base_url}/api/embed",
            {"model": embedding_model, "input": text, "truncate": True},
            timeout=60,
        )
        vectors = data.get("embeddings") or []
        if not vectors:
            raise ProviderError("Ollama không trả về embedding vector.")
        return vectors[0]


class GeminiProvider(BaseProvider):
    provider_name = "gemini"

    def __init__(self, config):
        super().__init__(config)
        api_key = config.get("api_key") or ""
        self.headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _generate(self, contents, system=None, json_mode=False):
        payload = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        generation_config = {"temperature": 0.2}
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        payload["generationConfig"] = generation_config
        data = self._post(
            f"{self.base_url}/models/{self.model}:generateContent",
            payload,
            headers=self.headers,
        )
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError("Gemini không trả về candidate hợp lệ.")
        return candidates[0].get("content") or {"role": "model", "parts": []}

    @staticmethod
    def _content_text(content):
        return "\n".join(
            part.get("text", "")
            for part in content.get("parts") or []
            if part.get("text")
        ).strip()

    def structured_json(self, prompt, system=None):
        content = self._generate(
            [{"role": "user", "parts": [{"text": prompt}]}],
            system=system,
            json_mode=True,
        )
        raw = self._content_text(content).replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError("Gemini không trả về JSON hợp lệ.") from exc

    def generate_text(self, prompt, system=None, max_tokens=1200):
        content = self._generate(
            [{"role": "user", "parts": [{"text": prompt}]}],
            system=system,
        )
        return self._content_text(content)


class AIProviderFactory:
    PROVIDERS = {
        "gemini": GeminiProvider,
        "ollama": OllamaProvider,
    }

    @classmethod
    def build(cls, provider_name, config):
        provider_cls = cls.PROVIDERS.get(provider_name)
        if not provider_cls:
            raise ProviderError(
                f"Provider '{provider_name}' không hỗ trợ kiến trúc adapter enterprise."
            )
        if config.get("local_only") and provider_name == "ollama":
            hostname = (urlparse(config.get("base_url") or "").hostname or "").lower()
            local_hostname = (
                hostname == "localhost"
                or hostname.endswith(".local")
                or (hostname and "." not in hostname)
            )
            try:
                address = ipaddress.ip_address(hostname)
                local_ip = address.is_private or address.is_loopback
            except ValueError:
                local_ip = False
            if not local_hostname and not local_ip:
                raise ProviderError(
                    "Ollama chỉ được kết nối qua localhost hoặc mạng riêng."
                )
        return provider_cls(config)
