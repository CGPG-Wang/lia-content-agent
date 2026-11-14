"""候选帧 Workflow v0.1 的测试：模拟 Qwen，验证多轮编排与磁盘留痕。

日常执行不会连接 Qwen / DashScope，也不会使用真实 API Key。
最后一项测试用 FFmpeg 生成临时视频并实际抽帧；缺少 FFmpeg 时跳过。
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from lia_agent.workflows.keyframe_workflow import replace_candidate_final
from lia_agent.config import KEYFRAME_PROMPT, VIDEO_FPS
from lia_agent.workflows.keyframe_workflow import (
    start_recommendation,
    continue_recommendation,
    approve_candidate,
    replace_candidate_final,
)


def make_reply(*seconds: float, notes: str | None = None) -> str:
    """生成符合真实 Qwen 返回格式的候选 JSON 文本。"""
    if notes is None:
        notes = "不足三张候选" if len(seconds) < 3 else ""
    return json.dumps(
        {
            "candidates": [
                {
                    "rank": index,
                    "time_seconds": second,
                    "shot": f"镜头 {index}",
                    "reason": f"第 {index} 张画面清晰",
                    "potential_issues": ["轻微遮挡"] if index == 2 else [],
                }
                for index, second in enumerate(seconds, start=1)
            ],
            "notes": notes,
        },
        ensure_ascii=False,
    )


class FakeQwenClient:
    """只模拟网络接口；不接触真实 DashScope 服务。"""

    def __init__(self, replies: list[str | Exception]):
        self.replies = list(replies)
        self.upload_count = 0
        self.uploaded_video_paths: list[Path] = []
        self.requests: list[list[dict]] = []

    def upload_video(self, video_path):
        self.upload_count += 1
        self.uploaded_video_paths.append(Path(video_path))
        return "oss://fake-bucket/reference-video.mp4"

    def recommend(self, messages):
        # 深拷贝，确保测试检查的是【请求发出当时】的消息，而非后续修改的列表。
        self.requests.append(copy.deepcopy(messages))
        if not self.replies:
            raise AssertionError("测试未准备对应轮次的模拟 Qwen 回复")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {
            "status_code": 200,
            "output": {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": [{"text": reply}]},
                }]
            },
        }


class KeyframeWorkflowTests(unittest.TestCase):
    def setUp(self):
        """每项测试都建立一份独立的本地任务目录。"""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.task_dir = Path(self.tmp.name) / "测试REELS"
        self.records = self.task_dir / "workspace" / "candidates_recomm"
        self.records.mkdir(parents=True)
        (self.task_dir / "workspace" / "task.json").write_text(
            json.dumps({"scene_id": "SEEDANCE_MULTISHOT_SINGLE_KF_RESHAPE"}),
            encoding="utf-8",
        )
        self.video = self.task_dir / "reference-video.mp4"
        self.video.write_bytes(b"fake-video-for-mocked-tests")
        (self.records / "inputs.json").write_text(
            json.dumps({
                "saved_video_name": self.video.name,
                "extra_requirements": "优先选择人物正脸",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        # 模拟 task_store.py 初始化生成的 state.json
        (self.records / "state.json").write_text(
            json.dumps(
                {
                    "stage": "inputs_saved",
                    "current_round": 0,
                    "selected_candidate": None,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _fake_extract(video_path, candidates, output_dir):
        """模拟 FFmpeg：保留候选原字段并写出可检查的临时文件。"""
        result = []
        for item in candidates:
            filename = f"candidate_{item['rank']:02d}.png"
            (Path(output_dir) / filename).write_bytes(b"fake-image-bytes")
            result.append({
                **item,
                "image_file": filename,
                "actual_time_seconds": item["time_seconds"],
                "frame_index": item["rank"] * 5,
            })
        return result

    @contextmanager
    def mocked_ffmpeg(self):
        """大多数用例只验证 Workflow 编排，不重复调用真实 FFmpeg。"""
        with (
            patch(
                "lia_agent.workflows.keyframe_workflow.probe_video_frames",
                return_value=([(0, 0.0), (1, 1.0)], 4.0),
            ),
            patch(
                "lia_agent.workflows.keyframe_workflow.extract_candidate_frames",
                side_effect=self._fake_extract,
            ),
        ):
            yield

    def test_first_round_uploads_once_and_saves_all_results(self):
        """首轮：读取留痕输入、上传视频、发送正确消息、保存图片和 JSON。"""
        client = FakeQwenClient([make_reply(0.4, 1.4, 2.4)])
        with self.mocked_ffmpeg():
            session, result = start_recommendation(self.task_dir, client=client)

        self.assertEqual(client.upload_count, 1)
        self.assertEqual(
            [path.resolve() for path in client.uploaded_video_paths],
            [self.video.resolve()]
        )
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(len(client.requests[0]), 2)
        self.assertEqual(client.requests[0][0]["content"][0]["text"], KEYFRAME_PROMPT)
        first_user_content = client.requests[0][1]["content"]
        self.assertEqual(first_user_content[0]["video"], session.video_uri)
        self.assertEqual(first_user_content[0]["fps"], VIDEO_FPS)
        self.assertIn("优先选择人物正脸", first_user_content[1]["text"])

        self.assertEqual(session.current_round, 1)
        self.assertFalse(session.failed)
        self.assertEqual(result.round_number, 1)
        self.assertEqual(result.status, "awaiting_decision")
        self.assertEqual(
            result.round_dir.resolve(),
            (self.records / "round_01").resolve()
        )
        self.assertEqual(len(result.candidates), 3)
        self.assertTrue((result.round_dir / "reply.txt").is_file())

        saved = json.loads((result.round_dir / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(
            Path(saved["source_video"]).resolve(),
            self.video.resolve()
        )
        self.assertEqual([c["time_seconds"] for c in saved["candidates"]], [0.4, 1.4, 2.4])
        for index in (1, 2, 3):
            self.assertTrue((result.round_dir / f"candidate_{index:02d}.png").is_file())
        self.assertFalse((result.round_dir / "user_feedback.txt").exists())

        # 验证第一轮推荐完成后，state.json 正确更新
        state = json.loads(
            (self.records / "state.json").read_text(encoding="utf-8")
        )

        self.assertEqual(state["stage"], "awaiting_decision")
        self.assertEqual(state["current_round"], 1)
        self.assertIsNone(state["selected_candidate"])

    def test_second_round_uses_full_history_and_saves_feedback(self):
        """第二轮：不重新上传视频，带上第一轮 Qwen 回复和用户反馈。"""
        first = make_reply(0.4, 1.4, 2.4)
        second = make_reply(0.6, 1.6, 2.6)
        client = FakeQwenClient([first, second])
        feedback = "第二张脸不清晰，请换一个正脸画面"
        with self.mocked_ffmpeg():
            session, first_result = start_recommendation(self.task_dir, client=client)
            second_result = continue_recommendation(session, feedback, client=client)

        self.assertEqual(client.upload_count, 1)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(client.requests[1]), 4)
        self.assertEqual(client.requests[1][2], {
            "role": "assistant", "content": [{"text": first}],
        })
        self.assertEqual(client.requests[1][3], {
            "role": "user", "content": [{"text": feedback}],
        })
        self.assertEqual(
            (first_result.round_dir / "user_feedback.txt").read_text(encoding="utf-8"),
            feedback,
        )
        self.assertEqual(session.current_round, 2)
        self.assertEqual(second_result.round_number, 2)
        self.assertEqual(second_result.status, "awaiting_decision")
        self.assertTrue((second_result.round_dir / "candidates.json").exists())
        self.assertEqual(len(second_result.candidates), 3)
        self.assertTrue((first_result.round_dir / "candidate_01.png").exists())

    def test_third_round_keeps_previous_messages(self):
        """第三轮仍保留首轮视频引用、两次模型回复和两次人工反馈。"""
        client = FakeQwenClient([
            make_reply(0.4, 1.4, 2.4),
            make_reply(0.6, 1.6, 2.6),
            make_reply(0.8, 1.8, 2.8),
        ])
        with self.mocked_ffmpeg():
            session, _ = start_recommendation(self.task_dir, client=client)
            continue_recommendation(session, "反馈A", client=client)
            result = continue_recommendation(session, "反馈B", client=client)

        self.assertEqual(result.round_number, 3)
        self.assertEqual(session.current_round, 3)
        self.assertEqual(client.upload_count, 1)
        self.assertEqual([len(request) for request in client.requests], [2, 4, 6])
        self.assertEqual(client.requests[2][-1]["content"][0]["text"], "反馈B")
        self.assertEqual(client.requests[0][1], client.requests[2][1])
        self.assertTrue((self.records / "round_03" / "candidates.json").exists())

    def test_empty_feedback_uses_default_instruction(self):
        """用户不填写反馈时，系统使用默认要求继续推荐。"""

        client = FakeQwenClient([
            make_reply(0.4, 1.4, 2.4),
            make_reply(0.6, 1.6, 2.6),
        ])

        with self.mocked_ffmpeg():
            # 第一轮推荐
            session, first = start_recommendation(
                self.task_dir,
                client=client,
            )

            # 第二轮：用户反馈为空
            second = continue_recommendation(
                session,
                "",
                client=client,
            )

        # 不应重复上传视频
        self.assertEqual(client.upload_count, 1)

        # Qwen 应当收到两次推荐请求
        self.assertEqual(len(client.requests), 2)

        # 第二轮应该成功创建
        self.assertEqual(second.round_number, 2)

        # 系统默认反馈应该保存到第一轮目录
        feedback_file = first.round_dir / "user_feedback.txt"

        self.assertTrue(feedback_file.is_file())

        saved_feedback = feedback_file.read_text(encoding="utf-8")

        self.assertIn("用户没有提供额外修改意见", saved_feedback)

        # 检查 Qwen 第二轮实际收到的反馈
        second_request = client.requests[1]

        self.assertEqual(
            second_request[-1]["content"][0]["text"],
            saved_feedback,
        )

    def test_existing_round_blocks_restarting_session(self):
        """已有 round_01 的任务不得用 start() 覆盖或重复上传。"""
        (self.records / "round_01").mkdir()
        client = FakeQwenClient([make_reply(0.4, 1.4, 2.4)])
        with self.assertRaises(FileExistsError):
            start_recommendation(self.task_dir, client=client)
        self.assertEqual(client.upload_count, 0)
        self.assertEqual(len(client.requests), 0)

    def test_missing_inputs_rejected_before_upload(self):
        """缺少 inputs.json 时不能发起任何云端调用。"""
        (self.records / "inputs.json").unlink()
        client = FakeQwenClient([make_reply(0.4, 1.4, 2.4)])
        with self.assertRaises(FileNotFoundError):
            start_recommendation(self.task_dir, client=client)
        self.assertEqual(client.upload_count, 0)

    def test_missing_video_rejected_before_upload(self):
        """inputs.json 指向的视频丢失时应在本地立即报错。"""
        self.video.unlink()
        client = FakeQwenClient([make_reply(0.4, 1.4, 2.4)])
        with self.assertRaises(FileNotFoundError):
            start_recommendation(self.task_dir, client=client)
        self.assertEqual(client.upload_count, 0)

    def test_invalid_json_keeps_reply_and_allows_next_round(self):
        """模型 JSON 不合法仍保留原文，允许用户通过反馈要求修正。"""
        client = FakeQwenClient(["not a json reply", make_reply(1.2, notes="只有一张")])
        with self.mocked_ffmpeg():
            session, bad = start_recommendation(self.task_dir, client=client)
            self.assertEqual(bad.status, "awaiting_decision")
            self.assertEqual(bad.candidates, [])
            self.assertIn("未通过校验", bad.notes)
            self.assertEqual(
                (bad.round_dir / "reply.txt").read_text(encoding="utf-8"),
                "not a json reply",
            )
            self.assertFalse((bad.round_dir / "candidates.json").exists())
            corrected = continue_recommendation(session, "请修正 JSON", client=client)

        self.assertEqual(corrected.round_number, 2)
        self.assertEqual(corrected.status, "awaiting_decision")
        self.assertEqual(len(corrected.candidates), 1)
        self.assertFalse(session.failed)

    def test_no_candidates_waiting_for_feedback(self):
        """Qwen 明确返回零候选，仍保存 JSON，且不调用 FFmpeg 抽帧。"""
        client = FakeQwenClient([make_reply(notes="未发现合适帧")])
        with self.mocked_ffmpeg(), patch(
            "lia_agent.workflows.keyframe_workflow.extract_candidate_frames"
        ) as extract:
            _session, result = start_recommendation(self.task_dir, client=client)
        self.assertEqual(result.status, "awaiting_decision")
        self.assertEqual(result.candidates, [])
        extract.assert_not_called()
        stored = json.loads((result.round_dir / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["candidates"], [])

    def test_feedback_refuses_overwrite_without_new_request(self):
        """若同一轮反馈已写入，拒绝再次覆盖，也不触发 API 请求。"""
        client = FakeQwenClient([make_reply(0.4, 1.4, 2.4)])
        with self.mocked_ffmpeg():
            session, first = start_recommendation(self.task_dir, client=client)
            (first.round_dir / "user_feedback.txt").write_text("已有反馈", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                continue_recommendation(session, "新反馈", client=client)
        self.assertEqual(session.current_round, 1)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(
            (first.round_dir / "user_feedback.txt").read_text(encoding="utf-8"),
            "已有反馈",
        )

    def test_qwen_failure_on_second_round_keeps_user_feedback(self):
        """第二轮请求异常时，保留反馈及上一轮 PNG，并禁止自动重发。"""
        client = FakeQwenClient([
            make_reply(0.4, 1.4, 2.4),
            RuntimeError("模拟 Qwen 连接失败"),
        ])
        with self.mocked_ffmpeg():
            session, first = start_recommendation(self.task_dir, client=client)
            with self.assertRaisesRegex(RuntimeError, "模拟 Qwen 连接失败"):
                continue_recommendation(session, "请再试三个角度", client=client)
            self.assertTrue(session.failed)
            self.assertEqual(session.current_round, 1)
            self.assertTrue((first.round_dir / "candidate_01.png").exists())
            self.assertEqual(
                (first.round_dir / "user_feedback.txt").read_text(encoding="utf-8"),
                "请再试三个角度",
            )
            self.assertTrue((self.records / "round_02").is_dir())
            self.assertFalse((self.records / "round_02" / "candidates.json").exists())
            with self.assertRaises(RuntimeError):
                continue_recommendation(session, "再次提交", client=client)
        self.assertEqual(len(client.requests), 2)

    def test_frame_extraction_failure_preserves_qwen_reply(self):
        """抽帧失败后原始回复保留，不应出现假成功的 candidates.json。"""
        client = FakeQwenClient([make_reply(0.4, 1.4, 2.4)])
        with patch(
            "lia_agent.workflows.keyframe_workflow.probe_video_frames",
            return_value=([(0, 0.0)], 4.0),
        ), patch(
            "lia_agent.workflows.keyframe_workflow.extract_candidate_frames",
            side_effect=RuntimeError("模拟 FFmpeg 抽帧失败"),
        ):
            with self.assertRaisesRegex(RuntimeError, "模拟 FFmpeg 抽帧失败"):
                start_recommendation(self.task_dir, client=client)
        round_dir = self.records / "round_01"
        self.assertTrue((round_dir / "reply.txt").exists())
        self.assertFalse((round_dir / "candidates.json").exists())

    def test_real_ffmpeg_creates_valid_pngs_through_workflow(self):
        """端到端离线联调：真实 FFmpeg 抽帧 + Workflow 留痕；Qwen 仍为模拟。"""
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("此测试需要本地安装 ffmpeg 和 ffprobe")
        subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=5",
                "-t", "3", "-c:v", "mpeg4", "-q:v", "4",
                "-y", str(self.video),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        client = FakeQwenClient([make_reply(0.2, 0.8, 1.4)])
        # 故意不 mock 两个 FFmpeg Service 函数，验证真实调用链。
        session, result = start_recommendation(self.task_dir, client=client)
        self.assertEqual(session.current_round, 1)
        self.assertEqual(result.status, "awaiting_decision")
        self.assertEqual(len(result.candidates), 3)
        stored = json.loads((result.round_dir / "candidates.json").read_text(encoding="utf-8"))
        for item in stored["candidates"]:
            image_path = result.round_dir / item["image_file"]
            self.assertTrue(image_path.is_file())
            self.assertTrue(image_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertIn("actual_time_seconds", item)
            self.assertIn("frame_index", item)
        self.assertEqual(client.upload_count, 1)
        self.assertEqual(len(client.requests), 1)


    def test_approve_candidate_updates_state(self):
        """用户确认候选帧后，更新 state.json 和审批记录。"""

        client = FakeQwenClient([
            make_reply(0.4, 1.4, 2.4)
        ])

        # 先模拟完成第一轮推荐
        with self.mocked_ffmpeg():
            session, result = start_recommendation(
                self.task_dir,
                client=client,
            )

        # 用户选择第 2 张候选帧
        selected_image = approve_candidate(
            task_dir=self.task_dir,
            round_number=1,
            rank=2,
        )

        # 检查关键帧图片
        self.assertTrue(selected_image.is_file())
        self.assertEqual(selected_image.name, "1f-original.png")

        # 检查 state.json
        state = json.loads(
            (self.records / "state.json").read_text(encoding="utf-8")
        )

        self.assertEqual(state["stage"], "candidate_approved")
        self.assertEqual(state["current_round"], 1)
        self.assertEqual(
            state["selected_candidate"]["selected_rank"], 2
        )

        # 检查 selection.json
        selection = json.loads(
            (self.records / "selection.json").read_text(encoding="utf-8")
        )

        self.assertEqual(selection["selected_round"], 1)
        self.assertEqual(selection["selected_rank"], 2)

        # 验证复制的是用户选择的第 2 张图片
        source = result.round_dir / "candidate_02.png"

        self.assertEqual(
            selected_image.read_bytes(),
            source.read_bytes(),
        )


    def _prepare_approved_task_for_final_replacement(self):
        """创建两轮推荐，并完成首次审批，供最终替换测试使用。"""

        client = FakeQwenClient([
            make_reply(0.4, 1.4, 2.4),
            make_reply(0.6, 1.6, 2.6),
        ])

        # 复用现有的 Qwen 模拟客户端与 FFmpeg 模拟环境
        with self.mocked_ffmpeg():
            session, first = start_recommendation(
                self.task_dir,
                client=client,
            )

            second = continue_recommendation(
                session,
                "请推荐不同的关键帧",
                client=client,
            )

        # 首次选择：第1轮候选1
        original_candidate = (
            first.round_dir / "candidate_01.png"
        )

        # 最终替换目标：第2轮候选2
        replacement_candidate = (
            second.round_dir / "candidate_02.png"
        )

        # 现有 Fake FFmpeg 为所有图片写入相同字节。
        # 这里人为区分内容，方便验证替换是否正确。
        original_candidate.write_bytes(
            b"original-image-data"
        )

        replacement_candidate.write_bytes(
            b"replacement-image-data"
        )

        # 调用已经测试过的首次审批 Workflow
        current_image = approve_candidate(
            task_dir=self.task_dir,
            round_number=1,
            rank=1,
        )

        self.assertTrue(current_image.is_file())
        self.assertEqual(
            current_image.read_bytes(),
            original_candidate.read_bytes(),
        )

        return (
            current_image,
            original_candidate,
            replacement_candidate,
            client,
        )


    def test_final_replacement_updates_image_and_records(self):
        """测试1：最终替换成功，旧图备份，审批历史和状态正确更新。"""

        (
            current_image,
            original_candidate,
            replacement_candidate,
            client,
        ) = self._prepare_approved_task_for_final_replacement()

        # 执行最终替换：选择第2轮候选2
        result = replace_candidate_final(
            task_dir=self.task_dir,
            round_number=2,
            rank=2,
        )

        backup = self.task_dir / "1f-original-bk.png"

        # 1. 新图片成为最终关键帧
        self.assertEqual(
            result.resolve(),
            current_image.resolve(),
        )

        self.assertEqual(
            current_image.read_bytes(),
            b"replacement-image-data",
        )

        # 2. 原来审批的图片得到备份
        self.assertTrue(backup.is_file())

        self.assertEqual(
            backup.read_bytes(),
            b"original-image-data",
        )

        # 3. 历史候选帧不能被修改
        self.assertEqual(
            original_candidate.read_bytes(),
            b"original-image-data",
        )

        self.assertEqual(
            replacement_candidate.read_bytes(),
            b"replacement-image-data",
        )

        # 4. 检查 selection.json
        selection_file = self.records / "selection.json"

        selection = json.loads(
            selection_file.read_text(encoding="utf-8")
        )

        self.assertEqual(selection["selected_round"], 2)
        self.assertEqual(selection["selected_rank"], 2)
        self.assertTrue(selection["final_replacement"])

        # 5. 审批历史必须保留两次选择
        history = selection["selection_history"]

        self.assertEqual(len(history), 2)

        self.assertEqual(
            history[0]["selected_round"], 1
        )
        self.assertEqual(
            history[0]["selected_rank"], 1
        )

        self.assertEqual(
            history[1]["selected_round"], 2
        )
        self.assertEqual(
            history[1]["selected_rank"], 2
        )

        # 两次审批都应有时间记录
        self.assertTrue(history[0]["confirmed_at"])
        self.assertTrue(history[1]["confirmed_at"])

        # 6. 检查 state.json
        state_file = self.records / "state.json"

        state = json.loads(
            state_file.read_text(encoding="utf-8")
        )

        self.assertEqual(
            state["stage"],
            "candidate_replaced_final",
        )

        self.assertEqual(state["current_round"], 2)

        self.assertEqual(
            state["selected_candidate"]["selected_round"],
            2,
        )

        self.assertEqual(
            state["selected_candidate"]["selected_rank"],
            2,
        )

        # 7. 不应留下临时文件
        self.assertFalse(
            (self.task_dir / "1f-original.png.new").exists()
        )

        self.assertFalse(
            (self.records / "selection.json.new").exists()
        )

        # 8. 最终替换不需要重新调用 Qwen
        self.assertEqual(client.upload_count, 1)
        self.assertEqual(len(client.requests), 2)


    def test_final_replacement_refuses_third_selection(self):
        """测试2：执行最终替换后，禁止第三次选择。"""

        (
            current_image,
            _,
            _,
            _,
        ) = self._prepare_approved_task_for_final_replacement()

        # 先执行一次合法的最终替换
        replace_candidate_final(
            self.task_dir,
            round_number=2,
            rank=2,
        )

        selection_file = self.records / "selection.json"
        state_file = self.records / "state.json"
        backup_file = self.task_dir / "1f-original-bk.png"

        # 记录替换成功后的全部重要文件
        image_before = current_image.read_bytes()
        backup_before = backup_file.read_bytes()
        selection_before = selection_file.read_bytes()
        state_before = state_file.read_bytes()

        # 再次尝试替换回第一轮候选3
        with self.assertRaises(ValueError):
            replace_candidate_final(
                self.task_dir,
                round_number=1,
                rank=3,
            )

        # 第三次选择被拒绝，所有文件必须保持不变
        self.assertEqual(
            current_image.read_bytes(),
            image_before,
        )

        self.assertEqual(
            backup_file.read_bytes(),
            backup_before,
        )

        self.assertEqual(
            selection_file.read_bytes(),
            selection_before,
        )

        self.assertEqual(
            state_file.read_bytes(),
            state_before,
        )


    def test_final_replacement_rejects_invalid_candidate(self):
        """测试3：无效候选不能破坏已有审批记录。"""

        (
            current_image,
            _,
            _,
            _,
        ) = self._prepare_approved_task_for_final_replacement()

        selection_file = self.records / "selection.json"
        state_file = self.records / "state.json"

        # 保存替换前的内容
        original_image = current_image.read_bytes()
        original_selection = selection_file.read_bytes()
        original_state = state_file.read_bytes()

        invalid_cases = [
            (2, 99),  # 第2轮不存在候选99
            (99, 1),  # 不存在第99轮
            (1, 1),   # 与首次审批的候选相同
        ]

        for round_number, rank in invalid_cases:

            with self.subTest(
                round_number=round_number,
                rank=rank,
            ):

                with self.assertRaises(ValueError):
                    replace_candidate_final(
                        self.task_dir,
                        round_number=round_number,
                        rank=rank,
                    )

                # 原始关键帧未被修改
                self.assertEqual(
                    current_image.read_bytes(),
                    original_image,
                )

                # selection.json 未被修改
                self.assertEqual(
                    selection_file.read_bytes(),
                    original_selection,
                )

                # state.json 未被修改
                self.assertEqual(
                    state_file.read_bytes(),
                    original_state,
                )

                # 不应该生成备份
                self.assertFalse(
                    (
                        self.task_dir / "1f-original-bk.png"
                    ).exists()
                )

                # 不应该产生临时文件
                self.assertFalse(
                    (
                        self.task_dir / "1f-original.png.new"
                    ).exists()
                )


    def test_legacy_backup_blocks_final_replacement(self):
        """旧版备份存在时，也必须禁止最终替换。"""

        (
            current_image,
            _,
            _,
            _,
        ) = self._prepare_approved_task_for_final_replacement()

        # 模拟历史任务中的旧版备份文件
        legacy_backup = (
            self.task_dir / "1f-original.png.bk"
        )

        legacy_backup.write_bytes(
            b"legacy-backup-image-data"
        )

        # 保存原始数据用于比较
        image_before = current_image.read_bytes()

        selection_file = self.records / "selection.json"
        selection_before = selection_file.read_bytes()

        state_file = self.records / "state.json"
        state_before = state_file.read_bytes()

        # 即使 state.json 尚未标记最终替换，
        # 只要旧版备份存在，也不能再次执行替换
        with self.assertRaises(ValueError):
            replace_candidate_final(
                self.task_dir,
                round_number=2,
                rank=2,
            )

        # 当前图片不能变化
        self.assertEqual(
            current_image.read_bytes(),
            image_before,
        )

        # 旧版备份不能被修改或删除
        self.assertEqual(
            legacy_backup.read_bytes(),
            b"legacy-backup-image-data",
        )

        # 审批记录不能变化
        self.assertEqual(
            selection_file.read_bytes(),
            selection_before,
        )

        # 任务状态不能变化
        self.assertEqual(
            state_file.read_bytes(),
            state_before,
        )

        # 不能生成新版备份
        self.assertFalse(
            (
                self.task_dir / "1f-original-bk.png"
            ).exists()
        )



if __name__ == "__main__":
    unittest.main(verbosity=2)
