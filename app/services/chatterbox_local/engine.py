"""In-process Chatterbox TTS model wrapper and per-segment audio assembly.

Ported from the ThinkBeforeYouSpeak project's tts_chatterbox.py: loads
ChatterboxTTS (Resemble AI, MIT) once per process, clones a per-tone reference
voice for each planned segment, and assembles the segments (with pre/post
pause padding) into a single output file. `torch`/`torchaudio`/`chatterbox-tts`
are only imported lazily, inside the functions that need them, so importing
this module never requires those heavy optional dependencies to be installed.
"""
from __future__ import annotations

import os
import tempfile
import threading

from loguru import logger
from pydub import AudioSegment

from app.utils import utils

EXAGGERATION_DEFAULT = 0.5
CFG_WEIGHT_DEFAULT = 0.5
TEMPERATURE_DEFAULT = 0.8
TOP_P_DEFAULT = 1.0
REPETITION_PENALTY_DEFAULT = 1.2

_GENERATION_PARAMS = (
    ("exaggeration", EXAGGERATION_DEFAULT),
    ("cfg_weight", CFG_WEIGHT_DEFAULT),
    ("temperature", TEMPERATURE_DEFAULT),
    ("top_p", TOP_P_DEFAULT),
    ("repetition_penalty", REPETITION_PENALTY_DEFAULT),
)

_model_singleton = None
_model_lock = threading.Lock()


def resolve_device(preference: str = "auto") -> str:
    import torch

    preference = (preference or "auto").strip().lower()
    if preference == "auto":
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    if preference == "mps" and not torch.backends.mps.is_available():
        logger.warning(
            "chatterbox-local: device 'mps' requested but not available, falling back to cpu"
        )
        return "cpu"
    if preference == "cuda" and not torch.cuda.is_available():
        logger.warning(
            "chatterbox-local: device 'cuda' requested but not available, falling back to cpu"
        )
        return "cpu"
    return preference


def get_chatterbox_model(device_preference: str = "auto"):
    """Module-level cached singleton: loaded once per process, not per call."""
    global _model_singleton
    if _model_singleton is not None:
        return _model_singleton

    with _model_lock:
        if _model_singleton is None:
            from chatterbox.tts import ChatterboxTTS

            device = resolve_device(device_preference)
            logger.info(f"chatterbox-local: loading Chatterbox model (device={device})...")
            _model_singleton = ChatterboxTTS.from_pretrained(device=device)
            logger.info("chatterbox-local: model loaded")

    return _model_singleton


def resolve_reference_wav(tone: str, catalog: dict, tone_dir: str) -> str:
    tones = catalog["tones"]
    if tone not in tones:
        raise ValueError(f"Tone '{tone}' does not exist in the catalog")

    wav_path = os.path.join(tone_dir, tones[tone]["reference_wav"])
    if not os.path.exists(wav_path):
        raise FileNotFoundError(f"Reference wav for tone '{tone}' not found: {wav_path}")
    return wav_path


def resolve_generation_params(segment: dict, catalog: dict) -> dict:
    """Per-segment override -> tone's base_params -> library default."""
    tone_params = catalog["tones"][segment["tone"]].get("base_params", {})
    return {
        name: segment.get(name, tone_params.get(name, default))
        for name, default in _GENERATION_PARAMS
    }


def _run_model_generate(model, text: str, reference_wav: str, params: dict):
    """Isolated so tests can patch this one call without a real torch/chatterbox install."""
    return model.generate(text, audio_prompt_path=reference_wav, **params)


def _save_segment_wav(wav, sample_rate: int, path: str) -> None:
    import torchaudio

    torchaudio.save(path, wav, sample_rate)


def synthesize_segments(
    segments: list[dict],
    catalog: dict,
    tone_dir: str,
    device_preference: str,
    output_path: str,
) -> list[tuple[str, float, float, float]]:
    """Render each planned segment, pad pauses, concatenate to `output_path` (mp3).

    Returns one (text, spoken_duration_seconds, pre_pause_sec, post_pause_sec)
    tuple per segment, in render order, for building accurate subtitle timing.
    """
    if not segments:
        raise ValueError("no segments to synthesize")

    ffmpeg_binary = utils.get_ffmpeg_binary()
    if ffmpeg_binary:
        AudioSegment.converter = ffmpeg_binary

    model = get_chatterbox_model(device_preference)
    sample_rate = model.sr

    combined = AudioSegment.empty()
    rendered: list[tuple[str, float, float, float]] = []

    with tempfile.TemporaryDirectory(prefix="chatterbox_local_") as tmp_dir:
        for i, seg in enumerate(segments):
            reference_wav = resolve_reference_wav(seg["tone"], catalog, tone_dir)
            params = resolve_generation_params(seg, catalog)

            wav = _run_model_generate(model, seg["text"], reference_wav, params)
            seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.wav")
            _save_segment_wav(wav, sample_rate, seg_path)

            audio = AudioSegment.from_wav(seg_path)
            pre_pause = float(seg.get("pre_pause_sec", 0.0) or 0.0)
            post_pause = float(seg.get("post_pause_sec", 0.0) or 0.0)

            if pre_pause > 0:
                combined += AudioSegment.silent(duration=int(pre_pause * 1000))
            combined += audio
            if post_pause > 0:
                combined += AudioSegment.silent(duration=int(post_pause * 1000))

            rendered.append((seg["text"], audio.duration_seconds, pre_pause, post_pause))

        combined.export(output_path, format="mp3")

    return rendered
