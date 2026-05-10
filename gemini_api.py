"""
gemini_api.py - Google Gemini Vision API client.

Sends captured images to Gemini 1.5 Flash for product detection
and returns structured JSON results.

Features:
  • Exponential back-off retry on transient errors
  • Strict JSON validation of Gemini response
  • Robust extraction even when model wraps JSON in markdown fences
"""

import json
import time
import base64
import logging
import mimetypes
from typing import Any

import requests

import config

logger = logging.getLogger("gemini_api")

# Gemini REST endpoint
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{config.GEMINI_MODEL}:generateContent"
)


def analyse_image(image_path: str) -> list[dict[str, Any]]:
    """
    Send an image to the Gemini Vision API and return detected products.

    Args:
        image_path: Absolute path to a JPEG image.

    Returns:
        A list of product dicts, or an empty list on failure / no products.
    """
    try:
        image_data = _encode_image(image_path)
    except Exception:
        logger.exception("Failed to read/encode image: %s", image_path)
        return []

    mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": config.GEMINI_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_data,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048,
        },
    }

    # Retry loop with exponential back-off
    for attempt in range(1, config.API_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                _GEMINI_URL,
                params={"key": config.GEMINI_API_KEY},
                json=payload,
                timeout=60,
            )

            if resp.status_code == 429 or resp.status_code >= 500:
                # Transient — retry
                wait = config.API_RETRY_BACKOFF_S ** attempt
                logger.warning(
                    "Gemini API %d (attempt %d/%d) — retrying in %.1fs",
                    resp.status_code,
                    attempt,
                    config.API_MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return _parse_response(resp.json())

        except requests.RequestException as exc:
            wait = config.API_RETRY_BACKOFF_S ** attempt
            logger.warning(
                "Gemini request error (attempt %d/%d): %s — retrying in %.1fs",
                attempt,
                config.API_MAX_RETRIES,
                exc,
                wait,
            )
            time.sleep(wait)

    logger.error("Gemini API failed after %d attempts", config.API_MAX_RETRIES)
    return []


def _encode_image(path: str) -> str:
    """Read an image file and return its base64-encoded string."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _parse_response(response_json: dict) -> list[dict[str, Any]]:
    """
    Extract and validate the JSON product list from the Gemini API
    response.  Handles cases where the model wraps JSON in markdown
    code fences.
    """
    try:
        text = (
            response_json["candidates"][0]["content"]["parts"][0]["text"]
        )
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("Unexpected Gemini response structure: %s", exc)
        logger.debug("Full response: %s", response_json)
        return []

    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Gemini returned invalid JSON:\n%s", text[:500])
        return []

    if not isinstance(parsed, list):
        logger.error("Gemini response is not a JSON array: %s", type(parsed))
        return []

    # Validate each product entry has expected keys
    required_keys = {"name", "brand", "product_type", "estimated_quantity"}
    validated: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            logger.warning("Skipping non-dict item in Gemini response")
            continue
        if not required_keys.issubset(item.keys()):
            logger.warning(
                "Skipping product with missing keys: %s",
                required_keys - item.keys(),
            )
            continue
        # Ensure sku_or_barcode key exists
        item.setdefault("sku_or_barcode", None)
        validated.append(item)

    logger.info("Gemini detected %d product(s)", len(validated))
    return validated
