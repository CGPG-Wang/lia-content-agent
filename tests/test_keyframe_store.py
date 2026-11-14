"""候选帧推荐 Storage 的独立单元测试（不联网、不调用 FFmpeg）。"""

import json
import re
import tempfile
import unittest
from pathlib import Path
from lia_agent.storage.keyframe_store import (
    create_round_dir,
    ensure_keyframe_root,
    load_recovery_task,
    save_round_candidates,
    save_round_feedback,
    save_round_reply,
    save_selection,
)


class KeyframeStoreTests(unittest.TestCase):
    def setUp(self):
        """每项测试使用全新的临时生产任务，不碰真实 REELS 目录。"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.task_dir = Path(self.temp_dir.name) / "2026-11-03_REELS"
        records = self.task_dir / "workspace"
        records.mkdir(parents=True)
        (records / "task.json").write_text(
            json.dumps({"schema_version": 1, "scene_id": "TEST_SCENE"}),
            encoding="utf-8",
        )
        self.keyframe_root = records / "candidates_recomm"

    def _round(self):
        """建立一个合法的轮次目录，供具体读写测试使用。"""
        _, round_dir = create_round_dir(self.task_dir)
        return round_dir

    @staticmethod
    def _sample_candidates():
        return {
            "candidates": [
                {
                    "rank": 1,
                    "time_seconds": 5.2,
                    "shot": "镜头01：人物正脸",
                    "reason": "面部清晰，背景完整",
                    "potential_issues": ["手臂略微遮挡"],
                }
            ],
            "notes": "本轮推荐说明",
        }

    @staticmethod
    def _sample_selection():
        return {
            "selected_round": 1,
            "selected_rank": 1,
            "selected_image": "round_01/candidate_01.png",
            "selected_time_seconds": 5.2,
        }

    def test_ensure_keyframe_root_creates_expected_directory(self):
        """建立正确的中文目录，重复调用仍返回同一位置。"""
        first = ensure_keyframe_root(self.task_dir)
        second = ensure_keyframe_root(self.task_dir)
        self.assertEqual(first, self.keyframe_root)
        self.assertEqual(second, first)
        self.assertTrue(first.is_dir())

    def test_ensure_keyframe_root_rejects_nonexistent_task(self):
        """没有 task.json 时，不能擅自创建一项新生产任务。"""
        missing_task = Path(self.temp_dir.name) / "not_created"
        with self.assertRaises(FileNotFoundError):
            ensure_keyframe_root(missing_task)
        self.assertFalse(missing_task.exists())

    def test_create_round_dir_increments_without_overwriting(self):
        """连续创建时轮次自动递增。"""
        number1, dir1 = create_round_dir(self.task_dir)
        number2, dir2 = create_round_dir(self.task_dir)
        self.assertEqual((number1, dir1.name), (1, "round_01"))
        self.assertEqual((number2, dir2.name), (2, "round_02"))
        self.assertTrue(dir1.is_dir())
        self.assertTrue(dir2.is_dir())
        self.assertNotEqual(dir1, dir2)

    def test_create_round_dir_uses_highest_existing_number(self):
        """即使中间有缺号，也在最大轮次之后创建。"""
        root = ensure_keyframe_root(self.task_dir)
        (root / "round_03").mkdir()
        (root / "round_01").mkdir()
        number, path = create_round_dir(self.task_dir)
        self.assertEqual(number, 4)
        self.assertEqual(path.name, "round_04")

    def test_save_round_reply_preserves_raw_text(self):
        """保留 Qwen 回复的中文、换行及原始内容。"""
        round_dir = self._round()
        reply = '{"candidates": []}\n这是一段用于测试的原始回复。'
        path = save_round_reply(round_dir, reply)
        self.assertEqual(path, round_dir / "reply.txt")
        self.assertEqual(path.read_text(encoding="utf-8"), reply)

    def test_save_round_candidates_preserves_json_structure(self):
        """候选 JSON 保存后可重新解析且内容不变。"""
        round_dir = self._round()
        document = self._sample_candidates()
        path = save_round_candidates(round_dir, document)
        self.assertEqual(path, round_dir / "candidates.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), document)

    def test_save_round_feedback_preserves_user_words(self):
        """保存针对上一轮的反馈，以便下一轮 Qwen 使用。"""
        round_dir = self._round()
        feedback = "第二张脸不够清晰。\n请优先选择正脸。"
        path = save_round_feedback(round_dir, feedback)
        self.assertEqual(path, round_dir / "user_feedback.txt")
        self.assertEqual(path.read_text(encoding="utf-8"), feedback)

    def test_round_files_refuse_overwrite(self):
        """回复、候选数据、用户反馈均不能被意外覆盖。"""
        round_dir = self._round()
        document = self._sample_candidates()
        cases = [
            (save_round_reply, "最初回复", "不同回复", "reply.txt"),
            (save_round_candidates, document, {"candidates": []}, "candidates.json"),
            (save_round_feedback, "原反馈", "新反馈", "user_feedback.txt"),
        ]
        for save_fn, original, replacement, filename in cases:
            with self.subTest(filename=filename):
                path = save_fn(round_dir, original)
                original_bytes = path.read_bytes()
                with self.assertRaises(FileExistsError):
                    save_fn(round_dir, replacement)
                self.assertEqual(path.read_bytes(), original_bytes)

    def test_invalid_round_inputs_do_not_create_files(self):
        """拒绝空回复、空反馈及缺少 candidates 数组的数据。"""
        round_dir = self._round()
        with self.assertRaises(ValueError):
            save_round_reply(round_dir, "  ")
        with self.assertRaises(ValueError):
            save_round_feedback(round_dir, "\n")
        with self.assertRaises(ValueError):
            save_round_candidates(round_dir, {"notes": "没有候选数组"})
        with self.assertRaises(ValueError):
            save_round_candidates(round_dir, {"candidates": [], "bad": float("nan")})
        for filename in ("reply.txt", "user_feedback.txt", "candidates.json"):
            self.assertFalse((round_dir / filename).exists())

    def test_round_write_requires_precreated_directory(self):
        """写入函数不会偷偷创建缺失的 round 目录。"""
        missing_round = self.keyframe_root / "round_99"
        with self.assertRaises(FileNotFoundError):
            save_round_reply(missing_round, "Qwen 的回复")
        self.assertFalse(missing_round.exists())

    def test_save_selection_adds_local_confirmation_time(self):
        """保存选中信息并补充约定的本地时间格式。"""
        self._round()
        selected = self._sample_selection()
        path = save_selection(self.task_dir, selected)
        self.assertEqual(path, self.keyframe_root / "selection.json")
        saved = json.loads(path.read_text(encoding="utf-8"))
        for key, value in selected.items():
            self.assertEqual(saved[key], value)
        self.assertRegex(
            saved["confirmed_at"],
            r"^\d{4}-\d{2}-\d{2}｜\d{2}:\d{2}:\d{2}｜-?\d{2}:\d{2}$",
        )
        self.assertNotIn("confirmed_at", selected)  # 不修改调用者原始字典

    def test_save_selection_refuses_overwrite(self):
        """已有确认记录不能被第二次选择静默替换。"""
        first_path = save_selection(self.task_dir, self._sample_selection())
        original_bytes = first_path.read_bytes()
        second = self._sample_selection()
        second["selected_rank"] = 2
        with self.assertRaises(FileExistsError):
            save_selection(self.task_dir, second)
        self.assertEqual(first_path.read_bytes(), original_bytes)

    def test_save_selection_requires_complete_fields(self):
        """缺少必要字段时，不能生成 selection.json。"""
        with self.assertRaises(ValueError):
            save_selection(self.task_dir, {"selected_round": 1})
        self.assertFalse((self.keyframe_root / "selection.json").exists())


    def test_load_recovery_task_without_selection(self):
        """测试1：尚未审批的任务，能否恢复历史推荐。"""

        # 创建第一轮目录
        round_dir = self._round()

        # 模拟一张真实存在的候选图片
        image_file = round_dir / "candidate_01.png"
        image_file.write_bytes(b"fake-image-data")

        # 使用原有测试数据，补充图片文件名
        candidates = self._sample_candidates()
        candidates["candidates"][0]["image_file"] = "candidate_01.png"

        save_round_candidates(round_dir, candidates)

        # 模拟第一轮推荐完成后的 state.json
        state_file = self.keyframe_root / "state.json"

        state_file.write_text(
            json.dumps({
                "stage": "awaiting_decision",
                "current_round": 1,
                "selected_candidate": None,
            }),
            encoding="utf-8",
        )

        # 执行恢复任务读取
        recovery = load_recovery_task(self.task_dir)

        # 检查任务目录
        self.assertEqual(
            recovery["task_dir"],
            self.task_dir.resolve(),
        )

        # 检查状态
        self.assertEqual(
            recovery["state"]["stage"],
            "awaiting_decision",
        )

        # 用户尚未确认候选帧
        self.assertIsNone(recovery["selection"])
        self.assertIsNone(recovery["approved_image"])

        # 检查历史候选帧
        self.assertEqual(len(recovery["history"]), 1)

        first_round = recovery["history"][0]

        self.assertEqual(first_round["round_number"], 1)
        self.assertEqual(len(first_round["candidates"]), 1)

        self.assertEqual(
            first_round["candidates"][0]["image_file"],
            "candidate_01.png",
        )


    def test_load_recovery_task_with_approved_candidate(self):
        """测试2：已经审批的任务，能否恢复当前关键帧及审批记录。"""

        # 创建第一轮历史推荐
        round_dir = self._round()

        candidate_image = round_dir / "candidate_01.png"
        candidate_image.write_bytes(b"approved-image-data")

        candidates = self._sample_candidates()
        candidates["candidates"][0]["image_file"] = "candidate_01.png"

        save_round_candidates(round_dir, candidates)

        # 模拟用户已确认候选1
        selected = self._sample_selection()

        save_selection(self.task_dir, selected)

        # 模拟已生成的最终关键帧
        approved_image = self.task_dir / "1f-original.png"
        approved_image.write_bytes(candidate_image.read_bytes())

        # 模拟审批完成的任务状态
        state_file = self.keyframe_root / "state.json"

        state_file.write_text(
            json.dumps({
                "stage": "candidate_approved",
                "current_round": 1,
                "selected_candidate": selected,
            }),
            encoding="utf-8",
        )

        # 执行恢复任务读取
        recovery = load_recovery_task(self.task_dir)

        # 检查任务状态
        self.assertEqual(
            recovery["state"]["stage"],
            "candidate_approved",
        )

        # 检查审批记录
        self.assertEqual(
            recovery["selection"]["selected_round"],
            1,
        )

        self.assertEqual(
            recovery["selection"]["selected_rank"],
            1,
        )

        # 检查当前关键帧路径
        self.assertEqual(
            recovery["approved_image"].resolve(),
            approved_image.resolve(),
        )

        # 确认历史推荐仍然可以读取
        self.assertEqual(len(recovery["history"]), 1)
        self.assertEqual(
            len(recovery["history"][0]["candidates"]),
            1,
        )


    def test_load_recovery_task_rejects_missing_directory(self):
        """测试3：不存在的任务目录不能被自动创建。"""

        # 构造一个不存在的生产任务路径
        missing_dir = Path(self.temp_dir.name) / "不存在的任务"

        self.assertFalse(missing_dir.exists())

        # 恢复任务应当直接报错
        with self.assertRaises(FileNotFoundError):
            load_recovery_task(missing_dir)

        # 最重要：不能因为读取操作而创建目录
        self.assertFalse(missing_dir.exists())



if __name__ == "__main__":
    unittest.main()
