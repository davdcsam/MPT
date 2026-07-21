import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pydub import AudioSegment

from app.services import documentary_sync
from app.services import voice


class TestPlanSegments(unittest.TestCase):
    def test_plan_segments_returns_empty_for_blank_script(self):
        self.assertEqual(documentary_sync.plan_segments(""), [])
        self.assertEqual(documentary_sync.plan_segments("   "), [])

    def test_plan_segments_merges_short_sentences_forward_to_minimum_duration(self):
        """Several very short sentences must be merged into one segment that
        meets the minimum estimated duration, instead of emitting one
        too-short segment per sentence."""
        script = "Yes. No. Ok. Sure. Wait. Really. Truly a longer sentence here."
        segments = documentary_sync.plan_segments(
            script, voice_rate=1.0, min_segment_seconds=4.5
        )

        self.assertGreater(len(segments), 0)
        for segment in segments[:-1]:
            self.assertGreaterEqual(
                voice.estimate_no_voice_duration(segment), 4.5
            )
        # far fewer segments than the 7 raw sentences - short ones got merged
        self.assertLess(len(segments), 7)
        # no words were dropped during merging
        joined_words = " ".join(segments).replace(".", "").split()
        original_words = script.replace(".", "").split()
        self.assertEqual(joined_words, original_words)

    def test_plan_segments_folds_short_trailing_remainder_into_previous_segment(self):
        """If the script ends before the last buffer reaches the minimum
        duration, it must never be emitted as its own too-short segment."""
        script = (
            "This is a much longer opening sentence meant to clear the minimum duration threshold on its own. "
            "Ok."
        )
        segments = documentary_sync.plan_segments(
            script, voice_rate=1.0, min_segment_seconds=4.5
        )

        self.assertEqual(len(segments), 1)
        self.assertIn("Ok", segments[0])

    def test_plan_segments_caps_segment_count_at_max_segments(self):
        script = " ".join(f"Sentence number {i}." for i in range(60))
        segments = documentary_sync.plan_segments(
            script, voice_rate=1.0, min_segment_seconds=0.1, max_segments=10
        )

        self.assertLessEqual(len(segments), 10)


class TestSynthesizeSegmentsGeneric(unittest.TestCase):
    def test_synthesize_segments_generic_raises_for_empty_segments(self):
        with self.assertRaises(ValueError):
            documentary_sync.synthesize_segments_generic(
                [], "voice", 1.0, 1.0, "/tmp/unused.mp3"
            )

    def test_synthesize_segments_generic_renders_and_measures_each_segment(self):
        def fake_tts(text, voice_name, voice_rate, voice_file, voice_volume):
            AudioSegment.silent(duration=500).export(voice_file, format="mp3")
            return object()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "audio.mp3")
            with patch.object(voice, "tts", side_effect=fake_tts):
                rendered = documentary_sync.synthesize_segments_generic(
                    ["First segment.", "Second segment."],
                    voice_name="voice",
                    voice_rate=1.0,
                    voice_volume=1.0,
                    output_path=output_path,
                )

            self.assertTrue(os.path.exists(output_path))
            self.assertEqual(len(rendered), 2)
            for text, duration, pre_pause, post_pause in rendered:
                self.assertAlmostEqual(duration, 0.5, delta=0.05)
                self.assertEqual(pre_pause, 0.0)
            # gap after every segment except the last
            self.assertGreater(rendered[0][3], 0.0)
            self.assertEqual(rendered[1][3], 0.0)

    def test_synthesize_segments_generic_falls_back_to_silent_audio_on_tts_failure(self):
        def fake_tts(text, voice_name, voice_rate, voice_file, voice_volume):
            return None  # simulates a provider failure for this segment

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "audio.mp3")
            with patch.object(voice, "tts", side_effect=fake_tts), patch.object(
                voice, "generate_silent_audio"
            ) as fake_silent:
                def _write_silent(duration_seconds, output_file):
                    AudioSegment.silent(duration=int(duration_seconds * 1000)).export(
                        output_file, format="mp3"
                    )
                    return True

                fake_silent.side_effect = _write_silent

                rendered = documentary_sync.synthesize_segments_generic(
                    ["Only segment."],
                    voice_name="voice",
                    voice_rate=1.0,
                    voice_volume=1.0,
                    output_path=output_path,
                )

            fake_silent.assert_called_once()
            self.assertEqual(len(rendered), 1)
            self.assertTrue(os.path.exists(output_path))

    def test_synthesize_segments_generic_raises_when_fallback_also_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "audio.mp3")
            with patch.object(voice, "tts", return_value=None), patch.object(
                voice, "generate_silent_audio", return_value=False
            ):
                with self.assertRaises(RuntimeError):
                    documentary_sync.synthesize_segments_generic(
                        ["Only segment."],
                        voice_name="voice",
                        voice_rate=1.0,
                        voice_volume=1.0,
                        output_path=output_path,
                    )


if __name__ == "__main__":
    unittest.main()
