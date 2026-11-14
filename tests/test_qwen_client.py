"""QwenClient 离线单元测试：不访问 DashScope、不使用真实 API Key。"""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lia_agent.config import KEYFRAME_OUTPUT_RULES, KEYFRAME_PROMPT, KEYFRAME_RULES
from lia_agent.services.qwen_client import (
    QWEN_MODEL,
    QwenClient,
    check_base_url,
    extract_reply_text,
    parse_candidates,
)


TEST_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
TEST_API_KEY = "fake-key-for-unit-tests"


def sample_candidates():
    """与旧原型约定一致的三个候选时间点。故意打乱 rank，测试排序。"""
    return {
        "candidates": [
            {"rank": 3, "time_seconds": 8.5, "shot": "镜头三", "reason": "服装清楚", "potential_issues": []},
            {"rank": 1, "time_seconds": 1.25, "shot": "镜头一", "reason": "五官清晰", "potential_issues": ["背景偏暗"]},
            {"rank": 2, "time_seconds": 5.0, "shot": "镜头二", "reason": "人物完整", "potential_issues": []},
        ],
        "notes": "",
    }


class QwenClientTests(unittest.TestCase):
    """验证 API 服务的输入、输出，以及错误处理。"""

    def make_client(self):
        """伪造 dashscope 和 requests 模块，保证测试绝不联网。"""
        sdk = MagicMock(name="fake_dashscope")
        http = MagicMock(name="fake_requests")
        with patch.dict(sys.modules, {"dashscope": sdk, "requests": http}):
            client = QwenClient(base_url=TEST_BASE_URL, api_key=TEST_API_KEY)
        return client, sdk, http

    def prepare_upload(self, http):
        """模拟百炼获取上传凭证及 OSS 上传成功。"""
        http.get.return_value.status_code = 200
        http.get.return_value.json.return_value = {
            "data": {
                "upload_host": "https://oss-cn-beijing.aliyuncs.com",
                "upload_dir": "unit-tests",
                "oss_access_key_id": "fake-access-key",
                "signature": "fake-signature",
                "policy": "fake-policy",
                "x_oss_object_acl": "private",
                "x_oss_forbid_overwrite": "true",
            }
        }
        http.post.return_value.status_code = 200

    def test_valid_base_url(self):
        self.assertEqual(check_base_url(TEST_BASE_URL + "/"), TEST_BASE_URL)

    def test_invalid_base_url(self):
        for address in (
            "http://dashscope.aliyuncs.com/api/v1",
            "https://unknown.example.com/api/v1",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    check_base_url(address)

    def test_client_uses_supplied_config(self):
        client, sdk, _ = self.make_client()
        self.assertEqual(client.base_url, TEST_BASE_URL)
        self.assertEqual(client.api_key, TEST_API_KEY)
        self.assertEqual(sdk.base_http_api_url, TEST_BASE_URL)

    def test_missing_api_key_is_rejected(self):
        with self.assertRaises(ValueError):
            QwenClient(base_url=TEST_BASE_URL, api_key=" ")

    def test_upload_video_success(self):
        client, _, http = self.make_client()
        self.prepare_upload(http)
        uploaded_content = []

        def capture_upload(url, **kwargs):
            uploaded_content.append(kwargs["files"]["file"][1].read())
            return http.post.return_value  # 使用单独的 Mock 返回值，不触发网络

        # 上面直接引用 post.return_value 安全：side_effect 执行时仅访问属性。
        http.post.side_effect = capture_upload
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "referece-vid.mov"
            video.write_bytes(b"fake video bytes")
            uri = client.upload_video(video)

        self.assertEqual(uri, "oss://unit-tests/referece-vid.mov")
        self.assertEqual(uploaded_content, [b"fake video bytes"])
        http.get.assert_called_once()
        http.post.assert_called_once()
        self.assertEqual(http.get.call_args.kwargs["params"]["model"], QWEN_MODEL)
        self.assertEqual(http.post.call_args.kwargs["data"]["key"], "unit-tests/referece-vid.mov")

    def test_upload_missing_or_empty_video(self):
        client, _, http = self.make_client()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.mp4"
            with self.assertRaises(ValueError):
                client.upload_video(path)  # 文件不存在
            path.touch()
            with self.assertRaises(ValueError):
                client.upload_video(path)  # 文件为空
        http.get.assert_not_called()
        http.post.assert_not_called()

    def test_upload_exceeds_size_limit(self):
        client, _, http = self.make_client()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.mp4"
            path.write_bytes(b"123")
            with patch("lia_agent.services.qwen_client.MAX_VIDEO_BYTES", 2):
                with self.assertRaises(ValueError):
                    client.upload_video(path)
        http.get.assert_not_called()

    def test_upload_policy_error(self):
        client, _, http = self.make_client()
        http.get.return_value.status_code = 403
        http.get.return_value.json.return_value = {
            "code": "Forbidden", "message": "模拟拒绝", "request_id": "test-id"
        }
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            with self.assertRaises(RuntimeError):
                client.upload_video(video)
        http.post.assert_not_called()

    def test_upload_rejects_untrusted_host(self):
        client, _, http = self.make_client()
        self.prepare_upload(http)
        http.get.return_value.json.return_value["data"]["upload_host"] = "https://evil.example.org"
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            with self.assertRaises(RuntimeError):
                client.upload_video(video)
        http.post.assert_not_called()

    def test_upload_post_error(self):
        client, _, http = self.make_client()
        self.prepare_upload(http)
        http.post.return_value.status_code = 403
        http.post.return_value.json.return_value = {
            "code": "UploadDenied", "message": "模拟上传失败", "request_id": "test-id"
        }
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"video")
            with self.assertRaises(RuntimeError):
                client.upload_video(video)

    def test_recommend_receives_full_conversation(self):
        """模拟两次请求：第二次应携带历史和反馈；Service 不负责追加历史。"""
        client, sdk, _ = self.make_client()
        recorded = []

        def fake_call(**kwargs):
            recorded.append(copy.deepcopy(kwargs))
            return {"status_code": 200, "output": {}}

        sdk.MultiModalConversation.call.side_effect = fake_call
        messages = [
            {"role": "system", "content": [{"text": KEYFRAME_PROMPT}]},
            {"role": "user", "content": [{"video": "oss://test/video.mp4", "fps": 2}]},
        ]
        self.assertIsInstance(client.recommend(messages), dict)
        messages.append({"role": "assistant", "content": [{"text": "第一次推荐"}]})
        messages.append({"role": "user", "content": [{"text": "请重新推荐"}]})
        client.recommend(messages)

        self.assertEqual(len(recorded), 2)
        self.assertEqual([len(record["messages"]) for record in recorded], [2, 4])
        self.assertEqual(recorded[1]["messages"][-1]["content"][0]["text"], "请重新推荐")
        self.assertEqual(recorded[0]["model"], QWEN_MODEL)
        self.assertFalse(recorded[0]["stream"])
        self.assertEqual(recorded[0]["api_key"], TEST_API_KEY)

    def test_extract_reply_text_success(self):
        payload = {
            "status_code": 200,
            "output": {"choices": [{
                "finish_reason": "stop",
                "message": {"content": [{"text": "第一段"}, {"text": "第二段"}]},
            }]},
        }
        self.assertEqual(extract_reply_text(payload), "第一段\n第二段")

    def test_extract_reply_text_rejects_errors(self):
        with self.assertRaises(RuntimeError):
            extract_reply_text({"status_code": 429, "code": "RateLimit"})
        with self.assertRaises(RuntimeError):
            extract_reply_text({"status_code": 200, "output": {"choices": []}})
        with self.assertRaises(RuntimeError):
            extract_reply_text({
                "status_code": 200,
                "output": {"choices": [{"finish_reason": "length", "message": {"content": [{"text": "未完成"}]}}]},
            })

    def test_parse_candidates_sorts_and_accepts_decimal_seconds(self):
        candidates = parse_candidates(json.dumps(sample_candidates(), ensure_ascii=False), 10.0)
        self.assertEqual([item["rank"] for item in candidates["candidates"]], [1, 2, 3])
        self.assertEqual(candidates["candidates"][0]["time_seconds"], 1.25)
        self.assertEqual(candidates["candidates"][0]["potential_issues"], ["背景偏暗"])

    def test_parse_candidates_with_markdown_fence(self):
        reply = "```json\n" + json.dumps(sample_candidates(), ensure_ascii=False) + "\n```"
        self.assertEqual(len(parse_candidates(reply, 10.0)["candidates"]), 3)

    def test_parse_candidates_rejects_bad_data(self):
        cases = []
        original = sample_candidates()
        data = copy.deepcopy(original)
        data["candidates"][0]["time_seconds"] = 10.0  # 等于视频结尾
        cases.append(data)
        data = copy.deepcopy(original)
        data["candidates"][0]["time_seconds"] = True
        cases.append(data)
        data = copy.deepcopy(original)
        data["candidates"][0]["time_seconds"] = 5.0  # 与另一张重复
        cases.append(data)
        data = copy.deepcopy(original)
        data["candidates"][0]["rank"] = 2  # 排名重复
        cases.append(data)
        data = copy.deepcopy(original)
        data["candidates"][0]["reason"] = ""
        cases.append(data)
        data = copy.deepcopy(original)
        data["candidates"][0]["potential_issues"] = "错误类型"
        cases.append(data)
        data = copy.deepcopy(original)
        data["candidates"] = data["candidates"][:2]  # 不足 3 张但 notes 为空
        cases.append(data)

        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_candidates(json.dumps(bad, ensure_ascii=False), 10.0)

    def test_parse_candidates_fewer_than_three_with_notes(self):
        data = sample_candidates()
        data["candidates"] = data["candidates"][:2]
        data["notes"] = "视频中只有两帧符合条件"
        self.assertEqual(len(parse_candidates(json.dumps(data), 10.0)["candidates"]), 2)

    def test_config_prompt_combines_rules(self):
        self.assertEqual(KEYFRAME_PROMPT, KEYFRAME_RULES + KEYFRAME_OUTPUT_RULES)


if __name__ == "__main__":
    unittest.main()
