import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

# add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.chatterbox_local import engine, tone_planner


def _write_silent_wav(path: str, duration_seconds: float, sample_rate: int = 24000) -> None:
    n_frames = int(duration_seconds * sample_rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


_VALID_LLM_JSON = """{
  "script": [
    {
      "segment": 1,
      "tone": "GANCHO",
      "text": "Hello there.",
      "reference": "impressive-happy.wav",
      "exaggeration": 0.7,
      "cfg_weight": 0.4,
      "temperature": 0.9,
      "pre_pause_sec": 0,
      "post_pause_sec": 0.15
    }
  ]
}"""


class TestToneCatalog(unittest.TestCase):
    def test_load_tone_catalog_bundled_default(self):
        catalog = tone_planner.load_tone_catalog()
        self.assertIn("default_tone", catalog)
        self.assertIn(catalog["default_tone"], catalog["tones"])

        tone_dir = catalog["_catalog_dir"]
        for tone_name, data in catalog["tones"].items():
            wav_path = os.path.join(tone_dir, data["reference_wav"])
            self.assertTrue(
                os.path.exists(wav_path),
                f"reference wav for tone '{tone_name}' does not exist: {wav_path}",
            )


class TestPlanScriptSegments(unittest.TestCase):
    def test_plan_script_segments_parses_valid_llm_json(self):
        catalog = tone_planner.load_tone_catalog()
        with patch(
            "app.services.chatterbox_local.tone_planner.generate_text_response",
            return_value=_VALID_LLM_JSON,
        ) as mocked:
            segments = tone_planner.plan_script_segments(
                "Hello there.", catalog, use_llm_planning=True
            )
        mocked.assert_called_once()
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["tone"], "GANCHO")
        self.assertEqual(segments[0]["text"], "Hello there.")

    def test_plan_script_segments_self_repairs_once_on_invalid_json(self):
        catalog = tone_planner.load_tone_catalog()
        with patch(
            "app.services.chatterbox_local.tone_planner.generate_text_response",
            side_effect=["not json at all", _VALID_LLM_JSON],
        ) as mocked, patch("app.services.chatterbox_local.tone_planner.time.sleep"):
            segments = tone_planner.plan_script_segments(
                "Hello there.", catalog, use_llm_planning=True
            )
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["tone"], "GANCHO")

    def test_plan_script_segments_falls_back_after_repeated_failures(self):
        catalog = tone_planner.load_tone_catalog()
        with patch(
            "app.services.chatterbox_local.tone_planner.generate_text_response",
            side_effect=["garbage", "still garbage", "still garbage again"],
        ) as mocked, patch("app.services.chatterbox_local.tone_planner.time.sleep"):
            segments = tone_planner.plan_script_segments(
                "Hello there.", catalog, use_llm_planning=True
            )
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["tone"], catalog["default_tone"])
        self.assertEqual(segments[0]["text"], "Hello there.")
        self.assertEqual(segments[0]["pre_pause_sec"], 0.0)
        self.assertEqual(segments[0]["post_pause_sec"], 0.0)

    def test_plan_script_segments_use_llm_planning_false_skips_llm_call(self):
        catalog = tone_planner.load_tone_catalog()
        with patch(
            "app.services.chatterbox_local.tone_planner.generate_text_response"
        ) as mocked:
            segments = tone_planner.plan_script_segments(
                "Hello there.", catalog, use_llm_planning=False
            )
        mocked.assert_not_called()
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["tone"], catalog["default_tone"])

    def test_plan_script_segments_use_llm_planning_false_splits_long_text(self):
        """A script long enough to exceed Chatterbox's ~40s/generate() ceiling
        must never come back as a single segment, even in the no-LLM fallback
        path — each generate() call is capped by the model itself, not by us."""
        catalog = tone_planner.load_tone_catalog()
        long_text = " ".join(f"This is sentence number {i}." for i in range(40))
        self.assertGreater(len(long_text), tone_planner._MAX_SEGMENT_CHARS)

        segments = tone_planner.plan_script_segments(
            long_text, catalog, use_llm_planning=False
        )

        self.assertGreater(len(segments), 1)
        for seg in segments:
            self.assertLessEqual(len(seg["text"]), tone_planner._MAX_SEGMENT_CHARS)
            self.assertEqual(seg["tone"], catalog["default_tone"])
        # every word from the source text must survive the split, in order
        self.assertEqual(
            " ".join(seg["text"] for seg in segments).split(), long_text.split()
        )


class TestSplitTextIntoChunks(unittest.TestCase):
    def test_split_text_into_chunks_keeps_short_text_whole(self):
        self.assertEqual(
            tone_planner._split_text_into_chunks("Hello there.", max_chars=320),
            ["Hello there."],
        )

    def test_split_text_into_chunks_splits_on_sentence_boundaries(self):
        text = "Short one. " + ("Padding word. " * 20) + "Final sentence here."
        chunks = tone_planner._split_text_into_chunks(text, max_chars=60)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 60)
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_split_text_into_chunks_force_splits_a_single_overlong_sentence(self):
        text = "word " * 100  # one giant "sentence" with no punctuation at all
        chunks = tone_planner._split_text_into_chunks(text.strip(), max_chars=30)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 30)


class TestResolveGenerationParams(unittest.TestCase):
    def test_resolve_generation_params_merges_segment_tone_and_library_defaults(self):
        catalog = {
            "default_tone": "CALM",
            "tones": {
                "CALM": {
                    "reference_wav": "calm.wav",
                    "base_params": {"exaggeration": 0.3, "cfg_weight": 0.6},
                }
            },
        }

        # segment overrides exaggeration only; cfg_weight falls back to the
        # tone's base_params; temperature/top_p/repetition_penalty fall back
        # to the library defaults.
        segment = {"tone": "CALM", "exaggeration": 0.55}
        params = engine.resolve_generation_params(segment, catalog)

        self.assertEqual(params["exaggeration"], 0.55)
        self.assertEqual(params["cfg_weight"], 0.6)
        self.assertEqual(params["temperature"], engine.TEMPERATURE_DEFAULT)
        self.assertEqual(params["top_p"], engine.TOP_P_DEFAULT)
        self.assertEqual(
            params["repetition_penalty"], engine.REPETITION_PENALTY_DEFAULT
        )


class TestSynthesizeSegments(unittest.TestCase):
    def test_synthesize_segments_pads_pauses_and_returns_durations(self):
        """No real torch/chatterbox-tts install required: the model singleton
        and the only two torch-touching calls are patched directly."""
        catalog = tone_planner.load_tone_catalog()
        tone_dir = catalog["_catalog_dir"]

        segments = [
            {"tone": "EXPLICACION", "text": "Hello", "pre_pause_sec": 0.0, "post_pause_sec": 0.2},
            {"tone": "TRIUNFO", "text": "World", "pre_pause_sec": 0.1, "post_pause_sec": 0.0},
        ]

        class _FakeModel:
            sr = 24000

        def _fake_save(wav, sample_rate, path):
            _write_silent_wav(path, duration_seconds=0.5, sample_rate=sample_rate)

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            engine, "get_chatterbox_model", return_value=_FakeModel()
        ), patch.object(
            engine, "_run_model_generate", return_value="fake-wav-tensor"
        ) as run_generate, patch.object(
            engine, "_save_segment_wav", side_effect=_fake_save
        ):
            output_path = str(Path(tmp_dir) / "output.mp3")
            rendered = engine.synthesize_segments(
                segments,
                catalog=catalog,
                tone_dir=tone_dir,
                device_preference="cpu",
                output_path=output_path,
            )

            self.assertEqual(run_generate.call_count, 2)
            self.assertTrue(os.path.exists(output_path))
            self.assertGreater(os.path.getsize(output_path), 0)

        self.assertEqual(len(rendered), 2)
        for text, duration, _pre, _post in rendered:
            self.assertAlmostEqual(duration, 0.5, delta=0.05)
        self.assertEqual((rendered[0][2], rendered[0][3]), (0.0, 0.2))
        self.assertEqual((rendered[1][2], rendered[1][3]), (0.1, 0.0))


if __name__ == "__main__":
    unittest.main()
