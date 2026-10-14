"""A deterministic stand-in model for tests and local demos. It costs nothing and needs no key."""
from __future__ import annotations

import json
import random
import time
from typing import Any, Dict, Tuple

from . import Provider, Review


class MockProvider(Provider):
    def __init__(self, family: str = "mock-a", rework_rate: float = 0.0, delay: float = 0.0):
        self.family = family
        self.model = f"{family}-model"
        self.rework_rate = rework_rate
        self.delay = delay

    def work(self, task: Dict[str, Any]) -> Tuple[str, str]:
        time.sleep(self.delay)
        brief = task["job"]["brief"].strip()
        key = task["step"]["key"]
        revised = "\n\n_Revised after review._" if task.get("rework_notes") else ""
        if key == "build":
            out = (
                "```html\n<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Preview</title>"
                "<style>body{font-family:system-ui;margin:0;padding:48px 20px;background:#0a090b;color:#f2ede4}"
                "h1{font-size:40px}</style></head><body><main><h1>Preview page</h1>"
                f"<p>{brief[:200]}</p></main></body></html>\n```"
            )
        elif key == "answer":
            out = json.dumps({
                "answer": f"Mock answer to: {brief[:80]}",
                "confidence": 0.5,
                "reasoning": "This answer comes from the mock provider, used for testing the pipeline.",
                "sources": [],
            })
        else:
            out = f"# {task['step']['title']}\n\nBrief: {brief}\n\n- Point one\n- Point two\n\n## Sources\n- https://example.com"
        return out + ("" if key in ("build", "answer") else revised), self.model

    def review(self, task: Dict[str, Any]) -> Review:
        time.sleep(self.delay)
        rng = random.Random(task["task_id"])
        if task["attempt"] == 1 and rng.random() < self.rework_rate:
            return Review("rework", "The output needs one more pass.", ["Add a short summary at the top."], self.model)
        return Review("accepted", "Meets the instruction.", [], self.model)
