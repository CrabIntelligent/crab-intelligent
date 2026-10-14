"""Optional OpenAI provider, mainly so reviews can come from a second model family.

Install with `pip install openai`, then set OPENAI_API_KEY and OPENAI_MODEL (a current model id
from your OpenAI account). This provider has no web search, so run it with `--kinds review`.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

from ..prompts import REVIEW_SYSTEM, WORK_SYSTEM, review_prompt, work_prompt
from . import Provider, ProviderError, Review, parse_review

_JSON_RULE = (
    '\n\nRespond with only a JSON object: {"verdict": "accepted" | "rework", '
    '"summary": string, "findings": [string, ...]}.'
)


class OpenAIProvider(Provider):
    family = "openai"

    def __init__(self, model: str = ""):
        try:
            from openai import OpenAI
        except ImportError:
            raise SystemExit("The openai package is not installed. Run: .venv/bin/pip install openai")
        self.model = model or os.environ.get("OPENAI_MODEL", "")
        if not self.model:
            raise SystemExit("Set OPENAI_MODEL to a current OpenAI model id (or pass --openai-model).")
        self.client = OpenAI()

    def _chat(self, system: str, prompt: str, json_mode: bool = False) -> Tuple[str, str]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # the openai package's error classes vary by version
            raise ProviderError(f"OpenAI API error: {exc}")
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise ProviderError("The model returned no text.")
        return text, resp.model

    def work(self, task: Dict[str, Any]) -> Tuple[str, str]:
        return self._chat(WORK_SYSTEM, work_prompt(task))

    def review(self, task: Dict[str, Any]) -> Review:
        text, model = self._chat(REVIEW_SYSTEM + _JSON_RULE, review_prompt(task), json_mode=True)
        try:
            return parse_review(json.loads(text), model)
        except ValueError:
            raise ProviderError("The reviewer's verdict was not valid JSON.")
