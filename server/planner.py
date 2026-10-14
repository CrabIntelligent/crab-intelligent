"""The dispatcher's catalogue: which jobs exist, and how each one splits into steps.

Phase 1 ships three job types end to end (report, website, oracle). The rest are listed so the
site and the API can show them, and they open as their pipelines are built.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

JOB_TYPES: Dict[str, Dict[str, Any]] = {
    "report": {
        "title": "Report",
        "summary": "A researched answer with sources",
        "available": True,
        "steps": [
            {
                "key": "outline",
                "title": "Research outline",
                "web": True,
                "instruction": (
                    "Plan the research. Restate the question in one sentence, list the sub-questions that "
                    "must be answered, and for each one note what evidence would settle it. Search the web "
                    "to find the most relevant, current sources and list them with URLs. Output Markdown."
                ),
            },
            {
                "key": "report",
                "title": "Write the report",
                "web": True,
                "instruction": (
                    "Write the full report in Markdown, following the accepted outline. Start with a short "
                    "summary that answers the question directly. Use headings for each sub-question. Support "
                    "every factual claim with a source, and end with a 'Sources' section listing each URL. "
                    "Say plainly where the evidence is thin or sources disagree."
                ),
            },
        ],
    },
    "website": {
        "title": "Website",
        "summary": "A one-page site, delivered as a single HTML file",
        "available": True,
        "steps": [
            {
                "key": "spec",
                "title": "Page spec",
                "web": False,
                "instruction": (
                    "Write the page spec in Markdown: purpose and audience, the sections in order with their "
                    "real copy (no lorem ipsum), the visual direction (palette as hex values, fonts from "
                    "Google Fonts, layout), and any interactions."
                ),
            },
            {
                "key": "build",
                "title": "Build the page",
                "web": False,
                "instruction": (
                    "Build the page from the accepted spec as one complete, self-contained HTML file: "
                    "<!doctype html>, inline CSS and JS, responsive down to 360px wide, accessible "
                    "(alt text, labels, visible focus). The only external requests allowed are Google Fonts. "
                    "Return only the file, inside a single ```html code block."
                ),
            },
        ],
    },
    "oracle": {
        "title": "Ask the oracle",
        "summary": "A signed answer",
        "available": True,
        "steps": [
            {
                "key": "answer",
                "title": "Answer",
                "web": True,
                "instruction": (
                    "Answer the question. Search the web when the answer depends on facts that could have "
                    "changed. Return only a JSON object with exactly these keys: "
                    '"answer" (string, direct and short), "confidence" (number from 0 to 1), '
                    '"reasoning" (string, at most 120 words), "sources" (array of URLs, may be empty).'
                ),
            }
        ],
    },
    "company": {"title": "Launch a company", "summary": "Programs, token and website", "available": False},
    "launchpad": {"title": "Create a launchpad", "summary": "Your own token launchpad on Meteora DBC", "available": False},
    "token": {"title": "Token", "summary": "Your token on a Meteora bonding curve", "available": False},
    "hook": {"title": "Transfer hook", "summary": "Your rules on every transfer (Token-2022)", "available": False},
    "contracts": {"title": "Smart contracts", "summary": "Pinocchio programs, with tests", "available": False},
    "audit": {"title": "Audit", "summary": "Your programs as they are", "available": False},
    "image": {"title": "Image", "summary": "One generated image", "available": False},
    "audio": {"title": "Audio", "summary": "Voice-over, speech or music", "available": False},
    "video": {"title": "Video", "summary": "One generated video", "available": False},
}


def catalogue() -> List[Dict[str, Any]]:
    return [
        {
            "key": key,
            "title": spec["title"],
            "summary": spec["summary"],
            "available": spec["available"],
            "steps": [s["title"] for s in spec.get("steps", [])],
        }
        for key, spec in JOB_TYPES.items()
    ]


def plan(job_type: str) -> List[Dict[str, Any]]:
    spec = JOB_TYPES.get(job_type)
    if not spec or not spec["available"]:
        raise ValueError(f"Job type '{job_type}' is not open yet")
    return [dict(step) for step in spec["steps"]]


def quote(job_type: str) -> Dict[str, Any]:
    # Pricing is set at launch. During the devnet preview every job is free.
    return {"amount": 0, "unit": None, "note": "Free during the devnet preview"}


# ---------- format gates: cheap checks before a step goes to review ----------

_FENCE = re.compile(r"```(?:html|HTML)?\s*\n(.*?)```", re.S)
_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def extract_html(text: str) -> Optional[str]:
    match = _FENCE.search(text)
    html = (match.group(1) if match else text).strip()
    return html if "<html" in html.lower() and "</html>" in html.lower() else None


def extract_oracle(text: str) -> Optional[Dict[str, Any]]:
    match = _JSON_FENCE.search(text)
    raw = (match.group(1) if match else text).strip()
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("answer"), str) or not isinstance(data.get("reasoning"), str):
        return None
    conf = data.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        return None
    sources = data.get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        return None
    return {"answer": data["answer"], "confidence": float(conf), "reasoning": data["reasoning"], "sources": sources}


def format_gate(job_type: str, step_key: str, output: str) -> Tuple[bool, str]:
    """Return (ok, problem). A failed gate sends the step straight back without spending a review."""
    if not output or not output.strip():
        return False, "The output was empty."
    if job_type == "website" and step_key == "build" and extract_html(output) is None:
        return False, "Return one complete HTML document (<html> ... </html>) inside a single ```html code block."
    if job_type == "oracle" and step_key == "answer" and extract_oracle(output) is None:
        return False, (
            'Return only a JSON object with keys "answer" (string), "confidence" (0 to 1), '
            '"reasoning" (string) and "sources" (array of URL strings).'
        )
    return True, ""
