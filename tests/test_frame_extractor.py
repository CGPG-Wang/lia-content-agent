"""FFmpeg 候选帧 Service：一部分不依赖工具，一部分使用真实 FFmpeg。"""

import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lia_agent.services.frame_extractor import (
    extract_candidate_frames,
    probe_video_frames,
)


class FrameExtractorTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.video = self.root / "reference-video.mp4"
        self.video.write_bytes(b"fake video")
        self.output = self.root / "round_01"
        self.output.mkdir()
        self.items = [
            {"rank": 1, "time_seconds": 0.2, "reason": "正脸清晰"},
            {"rank": 2, "time_seconds": 0.8, "reason": "服装完整"},
            {"rank": 3, "time_seconds": 1.4, "reason": "背景清楚"},
        ]

    def test_missing_video(self):
        with self.assertRaises(ValueError):
            extract_candidate_frames(self.root / "missing.mp4", self.items, self.output)

    def test_output_dir_must_exist(self):
        with self.assertRaises(ValueError):
            extract_candidate_frames(self.video, self.items, self.root / "not_yet_created")

    def test_rejects_bad_candidate_count(self):
        with self.assertRaises(ValueError):
            extract_candidate_frames(self.video, [], self.output)

    @patch("lia_agent.services.frame_extractor.probe_video_frames")
    def test_rejects_duplicate_frame(self, fake_probe):
        fake_probe.return_value = ([(0, 0.0), (1, 1.0), (2, 2.0)], 3.0)
        with self.assertRaisesRegex(ValueError, "同一实际视频帧"):
            extract_candidate_frames(
                self.video,
                [{"rank": 1, "time_seconds": 0.1},
                 {"rank": 2, "time_seconds": 0.2}],
                self.output,
            )
        self.assertEqual(list(self.output.iterdir()), [])

    @patch("lia_agent.services.frame_extractor.probe_video_frames")
    def test_rejects_out_of_range_time(self, fake_probe):
        fake_probe.return_value = ([(0, 0.0), (1, 1.0)], 2.0)
        with self.assertRaisesRegex(ValueError, "超出视频时长"):
            extract_candidate_frames(
                self.video, [{"rank": 1, "time_seconds": 2.0}], self.output
            )

    @patch("lia_agent.services.frame_extractor.probe_video_frames")
    def test_rejects_invalid_time(self, fake_probe):
        fake_probe.return_value = ([(0, 0.0), (1, 1.0), (2, 2.0)], 3.0)
        for invalid in (True, -1, float("nan"), float("inf"), "1.0"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    extract_candidate_frames(
                        self.video, [{"rank": 1, "time_seconds": invalid}], self.output
                    )

    @patch("lia_agent.services.frame_extractor.probe_video_frames")
    def test_no_overwrite(self, fake_probe):
        fake_probe.return_value = ([(0, 0.0), (1, 1.0), (2, 2.0)], 3.0)
        (self.output / "candidate_01.png").write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            extract_candidate_frames(self.video, self.items[:1], self.output)
        self.assertEqual((self.output / "candidate_01.png").read_bytes(), b"existing")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg")
    def test_real_ffmpeg_extracts_three_pngs(self):
        # 使用 FFmpeg 临时生成 2 秒测试素材，不需要从用户项目读取视频。
        subprocess.run([
            shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=5",
            "-t", "2", "-c:v", "mpeg4", "-q:v", "4", "-y", str(self.video),
        ], check=True, capture_output=True, text=True, timeout=60)

        frames, duration = probe_video_frames(self.video)
        self.assertGreaterEqual(len(frames), 10)
        self.assertGreater(duration, 1.9)
        result = extract_candidate_frames(self.video, self.items, self.output)
        self.assertEqual(len(result), 3)
        for idx, item in enumerate(result, 1):
            self.assertEqual(item["image_file"], f"candidate_{idx:02d}.png")
            self.assertTrue((self.output / item["image_file"]).stat().st_size > 100)
            self.assertTrue(math.isfinite(item["actual_time_seconds"]))
            self.assertIn("reason", item)
        # Service 不保存业务 JSON，后续由 Storage 处理。
        self.assertFalse((self.output / "candidates.json").exists())


if __name__ == "__main__":
    unittest.main()
