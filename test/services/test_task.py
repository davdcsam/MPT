import unittest
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import task as tm
from app.models.schema import MaterialInfo, VideoParams
from app.utils import utils

resources_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")
RUN_INTEGRATION_TESTS = os.environ.get("MPT_RUN_INTEGRATION_TESTS", "").lower() in {
    "1",
    "true",
    "yes",
}

class TestTaskService(unittest.TestCase):
    def setUp(self):
        pass
    
    def tearDown(self):
        pass

    def test_generate_script_forwards_advanced_prompt_options(self):
        """
        任务生成入口和 WebUI/API 共用 VideoParams。这里验证自动生成文案时，
        高级提示词参数会继续传到 LLM 服务层，避免只在 /scripts 接口生效。
        """
        params = VideoParams(
            video_subject="咖啡",
            video_script="",
            video_language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

        with patch.object(tm.llm, "generate_script", return_value="生成的文案") as generate:
            result = tm.generate_script("task-id", params)

        self.assertEqual(result, "生成的文案")
        generate.assert_called_once_with(
            video_subject="咖啡",
            language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

    def test_generate_terms_uses_script_order_mode_when_enabled(self):
        """
        默认模式不受影响；只有用户显式开启素材按文案顺序匹配时，任务层才
        要求 LLM 生成有序关键词，并适当增加关键词数量以覆盖更多脚本片段。
        """
        params = VideoParams(
            video_subject="城市通勤",
            video_script="",
            match_materials_to_script=True,
        )

        with patch.object(tm.llm, "generate_terms", return_value=["city", "train"]) as generate:
            result = tm.generate_terms("task-id", params, "先城市，再地铁")

        self.assertEqual(result, ["city", "train"])
        generate.assert_called_once_with(
            video_subject="城市通勤",
            video_script="先城市，再地铁",
            amount=8,
            match_script_order=True,
        )
    
    def test_generate_audio_uses_custom_file_inside_task_directory(self):
        task_id = "test-custom-audio-safe"
        task_dir = utils.task_dir(task_id)
        custom_audio_file = os.path.join(task_dir, "custom-audio.mp3")
        with open(custom_audio_file, "wb") as audio:
            audio.write(b"fake audio")

        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=custom_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.voice, "get_audio_duration", return_value=7),
            ):
                audio_file, audio_duration, sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, os.path.realpath(custom_audio_file))
        self.assertEqual(audio_duration, 7)
        self.assertIsNone(sub_maker)
        tts.assert_not_called()

    def test_generate_audio_accepts_server_side_custom_file(self):
        task_id = "test-custom-audio-server-side"
        task_dir = utils.task_dir(task_id)

        with tempfile.NamedTemporaryFile(suffix=".mp3") as server_audio:
            server_audio.write(b"fake audio")
            server_audio.flush()
            params = VideoParams(
                video_subject="custom audio",
                video_script="",
                custom_audio_file=server_audio.name,
                voice_name="test-voice",
            )

            try:
                with (
                    patch.object(tm.voice, "tts") as tts,
                    patch.object(tm.voice, "get_audio_duration", return_value=6),
                ):
                    audio_file, audio_duration, result_sub_maker = tm.generate_audio(
                        task_id, params, "script"
                    )
            finally:
                shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, os.path.realpath(server_audio.name))
        self.assertEqual(audio_duration, 6)
        self.assertIsNone(result_sub_maker)
        tts.assert_not_called()

    def test_generate_audio_rejects_missing_custom_file_without_tts(self):
        task_id = "test-custom-audio-missing"
        task_dir = utils.task_dir(task_id)
        missing_audio_file = os.path.join(task_dir, "missing.mp3")
        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=missing_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                audio_file, audio_duration, result_sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertIsNone(audio_file)
        self.assertIsNone(audio_duration)
        self.assertIsNone(result_sub_maker)
        tts.assert_not_called()
        update_task.assert_called_with(task_id, state=tm.const.TASK_STATE_FAILED)

    def test_generate_subtitle_uses_whisper_for_custom_audio_without_sub_maker(self):
        """
        自定义音频不会经过 TTS，所以没有 sub_maker。
        Whisper 可以直接从音频文件转写，此时不能被 sub_maker 为空的保护逻辑提前跳过。
        """
        task_id = "test-custom-audio-whisper-subtitle"
        task_dir = utils.task_dir(task_id)
        audio_file = os.path.join(task_dir, "custom-audio.mp3")
        Path(audio_file).write_bytes(b"fake audio")
        params = VideoParams(
            video_subject="custom audio",
            video_script="Hello world.",
            subtitle_enabled=True,
        )

        def fake_whisper_create(audio_file, subtitle_file):
            Path(subtitle_file).write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHello world.\n\n",
                encoding="utf-8",
            )

        try:
            with (
                patch.object(
                    tm.config,
                    "app",
                    dict(tm.config.app, subtitle_provider="whisper"),
                ),
                patch.object(
                    tm.subtitle, "create", side_effect=fake_whisper_create
                ) as create,
                patch.object(tm.subtitle, "correct") as correct,
            ):
                subtitle_path = tm.generate_subtitle(
                    task_id=task_id,
                    params=params,
                    video_script="Hello world.",
                    sub_maker=None,
                    audio_file=audio_file,
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertTrue(subtitle_path.endswith("subtitle.srt"))
        create.assert_called_once_with(audio_file=audio_file, subtitle_file=subtitle_path)
        correct.assert_called_once_with(
            subtitle_file=subtitle_path, video_script="Hello world."
        )

    def test_generate_subtitle_skips_edge_provider_without_sub_maker(self):
        """
        Edge 字幕依赖 TTS 返回的 sub_maker 时间轴。
        自定义音频缺少该对象时应继续跳过，避免产生不可信的字幕时间轴。
        """
        task_id = "test-custom-audio-edge-no-submaker"
        task_dir = utils.task_dir(task_id)
        audio_file = os.path.join(task_dir, "custom-audio.mp3")
        Path(audio_file).write_bytes(b"fake audio")
        params = VideoParams(
            video_subject="custom audio",
            video_script="Hello world.",
            subtitle_enabled=True,
        )

        try:
            with (
                patch.object(
                    tm.config,
                    "app",
                    dict(tm.config.app, subtitle_provider="edge"),
                ),
                patch.object(tm.voice, "create_subtitle") as create_subtitle,
                patch.object(tm.subtitle, "create") as whisper_create,
            ):
                subtitle_path = tm.generate_subtitle(
                    task_id=task_id,
                    params=params,
                    video_script="Hello world.",
                    sub_maker=None,
                    audio_file=audio_file,
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(subtitle_path, "")
        create_subtitle.assert_not_called()
        whisper_create.assert_not_called()

    @unittest.skipUnless(
        RUN_INTEGRATION_TESTS,
        "MPT_RUN_INTEGRATION_TESTS not set",
    )
    def test_task_local_materials(self):
        task_id = "00000000-0000-0000-0000-000000000000"
        video_materials=[]
        for i in range(1, 4):
            video_materials.append(MaterialInfo(
                provider="local",
                url=os.path.join(resources_dir, f"{i}.png"),
                duration=0
            ))

        params = VideoParams(
            video_subject="金钱的作用",
            video_script="金钱不仅是交换媒介，更是社会资源的分配工具。它能满足基本生存需求，如食物和住房，也能提供教育、医疗等提升生活品质的机会。拥有足够的金钱意味着更多选择权，比如职业自由或创业可能。但金钱的作用也有边界，它无法直接购买幸福、健康或真诚的人际关系。过度追逐财富可能导致价值观扭曲，忽视精神层面的需求。理想的状态是理性看待金钱，将其作为实现目标的工具而非终极目的。",
            video_terms="money importance, wealth and society, financial freedom, money and happiness, role of money",
            video_aspect="9:16",
            video_concat_mode="random",
            video_transition_mode="None",
            video_clip_duration=3,
            video_count=1,
            video_source="local",
            video_materials=video_materials,
            video_language="",
            voice_name="zh-CN-XiaoxiaoNeural-Female",
            voice_volume=1.0,
            voice_rate=1.0,
            bgm_type="random",
            bgm_file="",
            bgm_volume=0.2,
            subtitle_enabled=True,
            subtitle_position="bottom",
            custom_position=70.0,
            font_name="MicrosoftYaHeiBold.ttc",
            text_fore_color="#FFFFFF",
            text_background_color=True,
            font_size=60,
            stroke_color="#000000",
            stroke_width=1.5,
            n_threads=2,
            paragraph_number=1
        )
        result = tm.start(task_id=task_id, params=params)
        print(result)
    

class TestDocumentarySyncTaskWiring(unittest.TestCase):
    """documentary_sync_mode 的编排逻辑：只新增分支，不改动默认路径。"""

    def test_generate_audio_and_materials_by_segments_returns_none_when_no_segments_planned(
        self,
    ):
        params = VideoParams(video_subject="coffee", video_script="Hi.")
        with patch.object(tm.documentary_sync, "plan_segments", return_value=[]):
            result = tm.generate_audio_and_materials_by_segments(
                "task-id", params, "Hi."
            )
        self.assertEqual(result, (None, None, None, None, None))

    def test_generate_audio_and_materials_by_segments_returns_none_on_synthesis_failure(
        self,
    ):
        params = VideoParams(video_subject="coffee", video_script="Hi. There.")
        with (
            patch.object(
                tm.documentary_sync,
                "plan_segments",
                return_value=["Hi.", "There."],
            ),
            patch.object(
                tm.llm, "generate_segment_keywords", return_value=["coffee cup", "coffee pot"]
            ),
            patch.object(
                tm.documentary_sync,
                "synthesize_segments_generic",
                side_effect=RuntimeError("tts down"),
            ),
        ):
            result = tm.generate_audio_and_materials_by_segments(
                "task-id", params, "Hi. There."
            )
        self.assertEqual(result, (None, None, None, None, None))

    def test_generate_audio_and_materials_by_segments_returns_none_when_no_videos_downloaded(
        self,
    ):
        task_id = "test-doc-sync-no-videos"
        task_dir = utils.task_dir(task_id)
        params = VideoParams(video_subject="coffee", video_script="Hi. There.")
        rendered = [("Hi.", 1.0, 0.0, 0.15), ("There.", 1.2, 0.0, 0.0)]
        try:
            with (
                patch.object(
                    tm.documentary_sync,
                    "plan_segments",
                    return_value=["Hi.", "There."],
                ),
                patch.object(
                    tm.llm,
                    "generate_segment_keywords",
                    return_value=["coffee cup", "coffee pot"],
                ),
                patch.object(
                    tm.documentary_sync,
                    "synthesize_segments_generic",
                    return_value=rendered,
                ),
                patch.object(
                    tm.material, "download_videos_for_segments", return_value=[]
                ),
            ):
                result = tm.generate_audio_and_materials_by_segments(
                    task_id, params, "Hi. There."
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)
        self.assertEqual(result, (None, None, None, None, None))

    def test_generate_audio_and_materials_by_segments_happy_path(self):
        task_id = "test-doc-sync-happy-path"
        task_dir = utils.task_dir(task_id)
        params = VideoParams(
            video_subject="coffee",
            video_script="Hi. There.",
            voice_name="test-voice",
            voice_rate=1.0,
            voice_volume=1.0,
            documentary_min_segment_seconds=4.5,
        )
        rendered = [("Hi.", 1.0, 0.0, 0.15), ("There.", 1.2, 0.0, 0.0)]
        try:
            with (
                patch.object(
                    tm.documentary_sync,
                    "plan_segments",
                    return_value=["Hi.", "There."],
                ) as plan_segments,
                patch.object(
                    tm.llm,
                    "generate_segment_keywords",
                    return_value=["coffee cup", "coffee pot"],
                ) as generate_keywords,
                patch.object(
                    tm.documentary_sync,
                    "synthesize_segments_generic",
                    return_value=rendered,
                ) as synthesize,
                patch.object(
                    tm.material,
                    "download_videos_for_segments",
                    return_value=["v1.mp4", "v2.mp4"],
                ) as download,
            ):
                (
                    audio_file,
                    audio_duration,
                    sub_maker,
                    segment_video_paths,
                    segment_durations,
                ) = tm.generate_audio_and_materials_by_segments(
                    task_id, params, "Hi. There."
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        plan_segments.assert_called_once_with(
            "Hi. There.", voice_rate=1.0, min_segment_seconds=4.5
        )
        generate_keywords.assert_called_once_with("coffee", ["Hi.", "There."])
        synthesize.assert_called_once()
        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs["segment_keywords"], ["coffee cup", "coffee pot"])

        self.assertTrue(audio_file.endswith("audio.mp3"))
        self.assertEqual(audio_duration, 3)
        self.assertIsNotNone(sub_maker)
        self.assertEqual(segment_video_paths, ["v1.mp4", "v2.mp4"])
        self.assertEqual(segment_durations, [1.15, 1.2])

    def test_generate_final_videos_uses_segment_combiner_when_segment_durations_present(
        self,
    ):
        task_id = "test-doc-sync-final-videos-segments"
        task_dir = utils.task_dir(task_id)
        params = VideoParams(
            video_subject="coffee", video_script="Hi.", video_count=1
        )
        try:
            with (
                patch.object(tm.video, "combine_videos_by_segments") as combine_segments,
                patch.object(tm.video, "combine_videos") as combine_default,
                patch.object(tm.video, "generate_video"),
            ):
                tm.generate_final_videos(
                    task_id,
                    params,
                    ["ignored.mp4"],
                    "audio.mp3",
                    "subtitle.srt",
                    segment_video_paths=["v1.mp4", "v2.mp4"],
                    segment_durations=[1.15, 1.2],
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        combine_segments.assert_called_once()
        combine_default.assert_not_called()
        self.assertEqual(
            combine_segments.call_args.kwargs["segment_video_paths"], ["v1.mp4", "v2.mp4"]
        )
        self.assertEqual(
            combine_segments.call_args.kwargs["segment_durations"], [1.15, 1.2]
        )

    def test_generate_final_videos_uses_default_combiner_when_no_segment_durations(
        self,
    ):
        """回归测试：不传 segment_durations 时，行为与改动前完全一致。"""
        task_id = "test-doc-sync-final-videos-default"
        task_dir = utils.task_dir(task_id)
        params = VideoParams(
            video_subject="coffee", video_script="Hi.", video_count=1
        )
        try:
            with (
                patch.object(tm.video, "combine_videos_by_segments") as combine_segments,
                patch.object(tm.video, "combine_videos") as combine_default,
                patch.object(tm.video, "generate_video"),
            ):
                tm.generate_final_videos(
                    task_id,
                    params,
                    ["a.mp4", "b.mp4"],
                    "audio.mp3",
                    "subtitle.srt",
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        combine_default.assert_called_once()
        combine_segments.assert_not_called()

    def test_start_documentary_sync_mode_uses_segment_orchestrator_and_forces_video_count(
        self,
    ):
        task_id = "test-doc-sync-start"
        task_dir = utils.task_dir(task_id)
        params = VideoParams(
            video_subject="coffee",
            video_script="Hi. There.",
            documentary_sync_mode=True,
            video_count=2,
        )
        try:
            with (
                patch.object(tm, "generate_script", return_value="Hi. There."),
                patch.object(tm, "generate_terms") as generate_terms,
                patch.object(
                    tm,
                    "generate_audio_and_materials_by_segments",
                    return_value=(
                        os.path.join(task_dir, "audio.mp3"),
                        5,
                        object(),
                        ["v1.mp4", "v2.mp4"],
                        [1.15, 1.2],
                    ),
                ) as generate_audio_segments,
                patch.object(tm, "generate_subtitle", return_value="subtitle.srt"),
                patch.object(tm, "get_video_materials") as get_video_materials,
                patch.object(
                    tm,
                    "generate_final_videos",
                    return_value=(["final-1.mp4"], ["combined-1.mp4"]),
                ) as generate_final_videos,
            ):
                tm.start(task_id=task_id, params=params)
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        generate_terms.assert_not_called()
        get_video_materials.assert_not_called()
        generate_audio_segments.assert_called_once()
        self.assertEqual(params.video_count, 1)
        generate_final_videos.assert_called_once()
        self.assertEqual(
            generate_final_videos.call_args.kwargs["segment_video_paths"],
            ["v1.mp4", "v2.mp4"],
        )
        self.assertEqual(
            generate_final_videos.call_args.kwargs["segment_durations"],
            [1.15, 1.2],
        )


if __name__ == "__main__":
    unittest.main()
