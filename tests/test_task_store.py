
"""测试 Storage 模块的任务创建功能。"""

import io
import json
import tempfile
import unittest
from pathlib import Path

from lia_agent.storage.task_store import create_candidate_task


class TaskStoreTests(unittest.TestCase):

    def setUp(self):
        """每次测试前，创建独立的临时目录。"""

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.task_dir = (
            Path(self.temp_dir.name) / "2026-11-01_REELS"
        )

    def create_test_task(self):
        """提供统一的模拟用户输入。"""

        return create_candidate_task(
            task_directory=str(self.task_dir),
            scene_id="SEEDANCE_MULTISHOT_SINGLE_KF_RESHAPE",
            video_filename="reference.mov",
            video_stream=io.BytesIO(b"fake video content"),
            extra_requirements="优先推荐正脸清晰的画面",
        )

    def test_create_task_files(self):
        """测试1：能否正确创建目录及文件。"""

        result = self.create_test_task()

        self.assertEqual(result.resolve(), self.task_dir.resolve())

        video_path = self.task_dir / "reference-video.mov"
        self.assertTrue(video_path.exists())
        self.assertEqual(
            video_path.read_bytes(),
            b"fake video content"
        )

        records = self.task_dir / "workspace"

        self.assertTrue((records / "task.json").exists())

        candidate_dir = records / "candidates_recomm"

        self.assertTrue((candidate_dir / "inputs.json").exists())
        self.assertTrue((candidate_dir / "state.json").exists())

        state = json.loads(
            (candidate_dir / "state.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(state["stage"], "inputs_saved")
        self.assertEqual(state["current_round"], 0)
        self.assertIsNone(state["selected_candidate"])

        inputs = json.loads(
            (candidate_dir / "inputs.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            inputs["extra_requirements"],
            "优先推荐正脸清晰的画面"
        )

    def test_prevent_overwriting(self):
        """测试2：禁止新建任务覆盖已有结果。"""

        self.create_test_task()

        video_path = self.task_dir / "reference-video.mov"
        original_content = video_path.read_bytes()

        with self.assertRaises(FileExistsError):
            self.create_test_task()

        self.assertEqual(
            video_path.read_bytes(),
            original_content
        )

    def test_invalid_video_format(self):
        """测试3：拒绝不支持的视频格式。"""

        with self.assertRaises(ValueError):
            create_candidate_task(
                task_directory=str(self.task_dir),
                scene_id="SEEDANCE_MULTISHOT_SINGLE_KF_RESHAPE",
                video_filename="test.txt",
                video_stream=io.BytesIO(b"test"),
            )

        self.assertFalse(self.task_dir.exists())

    def test_task_directory_with_spaces(self):
        """测试路径前后存在空格时是否正确处理。"""

        result = create_candidate_task(
            task_directory=f"  {self.task_dir}  ",
            scene_id="SEEDANCE_MULTISHOT_SINGLE_KF_RESHAPE",
            video_filename="reference.mov",
            video_stream=io.BytesIO(b"fake video content"),
        )

        self.assertEqual(
            result.resolve(),
            self.task_dir.resolve()
        )

        self.assertTrue(
            (self.task_dir / "workspace" / "task.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
