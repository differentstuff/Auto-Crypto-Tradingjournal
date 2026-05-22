""" llm/response_parser.py -- Response validation and extraction for LLM outputs.

Handles:
  - JSON validation (check if response is valid JSON)
  - JSON extraction (strip markdown fences, extract JSON from mixed content)
  - Retry prompt generation for malformed responses

Design principle: "retry costs more than one-time-correct responses"
  BUT a free retry is cheaper than a broken pipeline needing manual intervention.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

_log = logging.getLogger(__name__)


def validate_json(response: str, role: str = "") -> tuple[bool, Optional[dict | list]]:
    """
    Validate that a response is valid JSON.

    Returns (is_valid, parsed_data).
    If valid, parsed_data is the Python object.
    If invalid, parsed_data is None.
    """
    try:
        parsed = json.loads(response)
        return True, parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting JSON from markdown fences or mixed content
    extracted = extract_json(response)
    if extracted:
        try:
            parsed = json.loads(extracted)
            return True, parsed
        except (json.JSONDecodeError, TypeError):
            pass

    _log.warning("Invalid JSON response for role '%s' (length=%d)", role, len(response))
    return False, None


def extract_json(text: str) -> Optional[str]:
    """
    Extract JSON from text that may contain markdown fences or prose.

    Handles:
      - ```json ... ``` fenced blocks
      - ``` ... ``` fenced blocks (language-agnostic)
      - JSON embedded in prose (first { or [ to last } or ])
      - Multiple JSON objects (returns the largest)
    """
    # Try markdown fence extraction first
    fence_patterns = [
        r"```json\s*\n?(.*?)\n?\s*```",  # ```json ... ```
        r"```\s*\n?(.*?)\n?\s*```",        # ``` ... ```
    ]
    for pattern in fence_patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        if matches:
            # Return the longest match (most likely the full JSON)
            return max(matches, key=len).strip()

    # Try finding JSON object or array boundaries
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = text.find(start_char)
        end_idx = text.rfind(end_char)
        if start_idx != -1 and end_idx > start_idx:
            candidate = text[start_idx:end_idx + 1]
            try:
                json.loads(candidate)  # Verify it's valid
                return candidate
            except json.JSONDecodeError:
                continue

    return None


def build_retry_prompt(
    original_prompt: str,
    original_system: Optional[str],
    response_format: Optional[str],
) -> tuple[str, Optional[str]]:
    """
    Build a stronger prompt for retrying a failed JSON response.

    Adds explicit format enforcement to both system and user prompts.
    Returns (enhanced_prompt, enhanced_system).
    """
    format_instruction = (
        "\n\nCRITICAL: Your previous response was not valid JSON. "
        "You MUST respond with ONLY valid JSON. "
        "No markdown. No explanation. No prose. "
        "Output a single JSON object or array starting with { or [."
    )

    enhanced_prompt = original_prompt + format_instruction

    enhanced_system = original_system
    if enhanced_system:
        enhanced_system += (
            "\n\nFORMAT REQUIREMENT: All responses must be valid JSON. "
            "No markdown fences. No prose outside the JSON structure."
        )
    else:
        enhanced_system = (
            "You are a JSON-only response system. "
            "All responses must be valid JSON with no additional text."
        )

    return enhanced_prompt, enhanced_system