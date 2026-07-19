"""Think-before-you-speak segment planning for the Chatterbox-local TTS provider.

Ported from the ThinkBeforeYouSpeak project's llm_guion.py: before any audio is
generated, an LLM reads the full script and splits it into short segments,
tagging each with an emotional tone (from a closed catalog) plus fine-tuned
Chatterbox generation params and pre/post pause timing. Reuses whichever LLM
provider MoneyPrinterTurbo is already configured with (via
app.services.llm.generate_text_response) instead of a dedicated client.
"""
from __future__ import annotations

import json
import os
import re

import yaml
from loguru import logger

from app.services.llm import generate_text_response

DEFAULT_TONE_CATALOG_PATH = os.path.join(
    os.path.dirname(__file__), "assets", "tone_catalog.yaml"
)
# 1 initial attempt + 1 self-repair retry, mirroring llm_guion.py's single retry.
_MAX_PLANNING_ATTEMPTS = 2

_REQUIRED_STRING_FIELDS = ("tone", "text", "reference")
_REQUIRED_NUMERIC_FIELDS = ("pre_pause_sec", "post_pause_sec")
_OPTIONAL_NUMERIC_01_FIELDS = ("exaggeration", "cfg_weight")

_SYSTEM_PROMPT_TEMPLATE = """You are a content producer and voice-over director.

Your task is to take a raw topic or script and split it into short blocks,
assigning each block an emotional tone label that will later be used to
clone a voice with a text-to-speech engine.

Respond ONLY in English. Available tone catalog (use EXACTLY these names,
uppercase):
{catalog_text}

Rules:
1. Translate the text to English. The text-to-speech engine is optimized for
   English; the "text" field of every segment must be natural, spoken
   English, regardless of the input script's language.
2. Do NOT summarize, shorten, or drop any sentence from the source script.
   Every idea and every sentence in the input must appear, translated, in
   some segment's "text". Merging two short related sentences into one
   segment is fine, but silently skipping a sentence is not.
3. You must use ONLY one of these tone labels: {tone_names}.
   Do not invent new tones. If none fits perfectly, pick the closest one.
4. Change tone every 20-30 seconds of approximate speech to keep retention.
5. Keep segments short and punchy: prefer one sentence, or two short ones,
   per segment. Split long sentences into multiple segments instead of
   writing a long block of speech.
6. Pacing must be tight and natural, like a fast-cut short-form video, not
   like a slow narrator. Set "pre_pause_sec" and "post_pause_sec" in seconds
   using these ranges:
   - 0.0-0.15s: segments that continue the same idea (default choice).
   - 0.15-0.3s: only between distinct ideas or before a change of tone.
   - 0.3-0.5s: reserve ONLY for the very final segment, or right before a
     big punchline/reveal. Use this sparingly (at most 1-2 times per script).
   Never default to a long pause; when in doubt, use a short one.
7. The "reference" field of every segment must be exactly the
   "reference_wav" value of the chosen tone, per the catalog above.
8. Besides "tone", tune the fine-grained parameters of each segment based on
   the real intensity of that specific line (not just the tone's average):
   - "exaggeration" (0.0-1.0): emotional intensity. Higher = more intense.
   - "cfg_weight" (0.0-1.0): fidelity to the cloned voice. Higher = more rigid.
   - "temperature" (0.0-1.0+): sampling variability. Higher = more expressive.
   Start from the base values shown per tone above, and deviate slightly
   based on what the line calls for.
9. Respond ONLY with the JSON, no explanations, no markdown, no code fences.
   The output format must be exactly:

{{
  "script": [
    {{
      "segment": 1,
      "tone": "GANCHO",
      "text": "...",
      "reference": "ref.wav",
      "exaggeration": 0.7,
      "cfg_weight": 0.4,
      "temperature": 0.9,
      "pre_pause_sec": 0,
      "post_pause_sec": 0.15
    }}
  ]
}}

Raw script (source language; translate every "text" field to English as
instructed):

{script_text}"""


def load_tone_catalog(path: str | None = None) -> dict:
    """Load a tone catalog YAML. Defaults to the bundled catalog."""
    catalog_path = path or DEFAULT_TONE_CATALOG_PATH
    with open(catalog_path, encoding="utf-8") as f:
        catalog = yaml.safe_load(f)
    catalog["_catalog_dir"] = os.path.dirname(os.path.abspath(catalog_path))
    return catalog


def build_tone_planning_prompt(text: str, catalog: dict) -> str:
    tones = catalog["tones"]
    tone_names = ", ".join(tones.keys())

    catalog_lines = []
    for name, data in tones.items():
        params = data.get("base_params", {})
        catalog_lines.append(
            f"- {name}: {data['intent']} "
            f"(reference: {data['reference_wav']}, "
            f"base exaggeration {params.get('exaggeration')}, "
            f"base cfg_weight {params.get('cfg_weight')}, "
            f"base temperature {params.get('temperature')})"
        )

    return _SYSTEM_PROMPT_TEMPLATE.format(
        catalog_text="\n".join(catalog_lines),
        tone_names=tone_names,
        script_text=text,
    )


def _extract_json(raw_response: str) -> str:
    text = (raw_response or "").strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in the LLM response")
    return text[start : end + 1]


def _validate_segments(data: dict, catalog: dict) -> list[str]:
    errors = []
    valid_tones = catalog["tones"]

    if not isinstance(data, dict) or "script" not in data:
        return ["the JSON is missing the 'script' key"]

    segments = data["script"]
    if not isinstance(segments, list) or len(segments) == 0:
        return ["'script' must be a non-empty list"]

    for i, seg in enumerate(segments):
        prefix = f"segment[{i}]"
        if not isinstance(seg, dict):
            errors.append(f"{prefix}: not an object")
            continue

        for field in _REQUIRED_STRING_FIELDS:
            if field not in seg:
                errors.append(f"{prefix}: missing field '{field}'")
            elif not isinstance(seg[field], str):
                errors.append(f"{prefix}: field '{field}' has invalid type")

        if isinstance(seg.get("text"), str) and not seg["text"].strip():
            errors.append(f"{prefix}: 'text' is empty")

        for field in _REQUIRED_NUMERIC_FIELDS:
            value = seg.get(field)
            if not isinstance(value, (int, float)):
                errors.append(f"{prefix}: missing/invalid field '{field}'")
            elif value < 0:
                errors.append(f"{prefix}: '{field}' cannot be negative")

        tone = seg.get("tone")
        if tone is not None:
            if tone not in valid_tones:
                errors.append(f"{prefix}: tone '{tone}' does not exist in the catalog")
            else:
                expected_reference = valid_tones[tone]["reference_wav"]
                if seg.get("reference") != expected_reference:
                    seg["reference"] = expected_reference

        for field in _OPTIONAL_NUMERIC_01_FIELDS:
            value = seg.get(field)
            if value is not None:
                if not isinstance(value, (int, float)):
                    errors.append(f"{prefix}: '{field}' has invalid type")
                elif not (0.0 <= value <= 1.0):
                    errors.append(f"{prefix}: '{field}' must be between 0.0 and 1.0")

        temperature = seg.get("temperature")
        if temperature is not None:
            if not isinstance(temperature, (int, float)):
                errors.append(f"{prefix}: 'temperature' has invalid type")
            elif temperature < 0.0:
                errors.append(f"{prefix}: 'temperature' cannot be negative")

    return errors


def _parse_and_validate(raw_response: str, catalog: dict) -> list[dict]:
    json_text = _extract_json(raw_response)
    data = json.loads(json_text)

    errors = _validate_segments(data, catalog)
    if errors:
        raise ValueError("; ".join(errors))

    return sorted(data["script"], key=lambda s: s.get("segment", 0))


def _fallback_single_segment(text: str, catalog: dict) -> list[dict]:
    default_tone = catalog["default_tone"]
    base_params = catalog["tones"][default_tone].get("base_params", {})
    return [
        {
            "segment": 1,
            "tone": default_tone,
            "text": text.strip(),
            "reference": catalog["tones"][default_tone]["reference_wav"],
            "exaggeration": base_params.get("exaggeration"),
            "cfg_weight": base_params.get("cfg_weight"),
            "temperature": base_params.get("temperature"),
            "pre_pause_sec": 0.0,
            "post_pause_sec": 0.0,
        }
    ]


def plan_script_segments(
    text: str, catalog: dict, use_llm_planning: bool = True
) -> list[dict]:
    """Split `text` into tone-tagged segments, never raising.

    When `use_llm_planning` is False, or the LLM call/validation keeps failing
    after one self-repair retry, falls back to a single segment using the
    catalog's default tone so a video never fails to generate audio because of
    this planning step.
    """
    text = (text or "").strip()
    if not text:
        return []

    if not use_llm_planning:
        return _fallback_single_segment(text, catalog)

    prompt = build_tone_planning_prompt(text, catalog)
    raw_response = ""
    last_error = ""

    for attempt in range(1, _MAX_PLANNING_ATTEMPTS + 1):
        try:
            raw_response = generate_text_response(prompt)
            if isinstance(raw_response, str) and raw_response.startswith("Error: "):
                raise ValueError(raw_response)
            return _parse_and_validate(raw_response, catalog)
        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"chatterbox-local tone planning attempt {attempt}/{_MAX_PLANNING_ATTEMPTS} "
                f"failed: {last_error}"
            )
            if attempt < _MAX_PLANNING_ATTEMPTS:
                prompt = (
                    f"{prompt}\n\nYour previous response was not valid JSON or failed "
                    f"validation: {last_error}\n\nPrevious response:\n{raw_response}\n\n"
                    "Respond only with the corrected JSON, no explanations, no markdown."
                )

    logger.warning(
        "chatterbox-local tone planning failed after all attempts "
        f"({last_error}); falling back to a single default-tone segment"
    )
    return _fallback_single_segment(text, catalog)
