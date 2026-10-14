"""Model providers. Each one belongs to a model family; reviews always go to a different family."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


class ProviderError(Exception):
    """The model could not finish this task (declined, truncated, API or network failure)."""


@dataclass
class Review:
    verdict: str
    summary: str
    findings: List[str] = field(default_factory=list)
    model: str = ""


class Provider:
    family = "base"
    model = ""

    def work(self, task: Dict[str, Any]) -> Tuple[str, str]:
        """Return (output text, model that served it)."""
        raise NotImplementedError

    def review(self, task: Dict[str, Any]) -> Review:
        raise NotImplementedError


def parse_review(data: Any, model: str) -> Review:
    if not isinstance(data, dict) or data.get("verdict") not in ("accepted", "rework"):
        raise ProviderError("The reviewer returned an invalid verdict.")
    findings = data.get("findings") or []
    if not isinstance(findings, list):
        findings = [str(findings)]
    return Review(verdict=data["verdict"], summary=str(data.get("summary", "")), findings=[str(f) for f in findings], model=model)
