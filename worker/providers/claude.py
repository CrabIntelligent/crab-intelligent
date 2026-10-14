"""Claude provider (Anthropic API). Needs ANTHROPIC_API_KEY or an `ant auth login` profile."""
from __future__ import annotations

import json
from typing import Any, Dict, Tuple

import anthropic

from ..prompts import REVIEW_SCHEMA, REVIEW_SYSTEM, WORK_SYSTEM, review_prompt, work_prompt
from . import Provider, ProviderError, Review, parse_review

# Server-side fallback: if a request is declined by a safety classifier, the API re-runs it on a
# fallback model chosen for that refusal category, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 6}
MAX_CONTINUATIONS = 4


class ClaudeProvider(Provider):
    family = "anthropic"

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high"):
        self.model = model
        self.effort = effort
        self.client = anthropic.Anthropic()

    def _run(self, system: str, prompt: str, *, web: bool = False,
             schema: Dict[str, Any] = None, max_tokens: int = 64000) -> Tuple[str, str]:
        output_config: Dict[str, Any] = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        params: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config=output_config,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if web:
            params["tools"] = [WEB_SEARCH_TOOL]

        blocks: list = []
        message = None
        for _ in range(MAX_CONTINUATIONS + 1):
            messages = [{"role": "user", "content": prompt}]
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            try:
                with self.client.beta.messages.stream(messages=messages, **params) as stream:
                    message = stream.get_final_message()
            except anthropic.RateLimitError as exc:
                raise ProviderError(f"Rate limited by the Anthropic API: {exc.message}")
            except anthropic.APIStatusError as exc:
                raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}")
            except anthropic.APIConnectionError:
                raise ProviderError("Could not reach the Anthropic API.")
            blocks = blocks + list(message.content)
            # pause_turn: the server-side tool loop hit its iteration limit; resend to let it resume.
            if message.stop_reason != "pause_turn":
                break

        if message.stop_reason == "refusal":
            category = getattr(message.stop_details, "category", None) if message.stop_details else None
            raise ProviderError(f"The model declined this step (category: {category}).")
        if message.stop_reason == "max_tokens":
            raise ProviderError("The output hit the token limit before it was finished.")
        text = "".join(b.text for b in blocks if b.type == "text").strip()
        if not text:
            raise ProviderError("The model returned no text.")
        return text, message.model

    def work(self, task: Dict[str, Any]) -> Tuple[str, str]:
        return self._run(WORK_SYSTEM, work_prompt(task), web=task["step"]["web"])

    def review(self, task: Dict[str, Any]) -> Review:
        text, model = self._run(REVIEW_SYSTEM, review_prompt(task), schema=REVIEW_SCHEMA, max_tokens=16000)
        try:
            data = json.loads(text)
        except ValueError:
            raise ProviderError("The reviewer's verdict was not valid JSON.")
        return parse_review(data, model)
