"""Qwen 候选帧推荐的底层 API 服务（不依赖 Streamlit）。

从已测试的 lia_qwen_keyframe_recommendation.py 中提取：
- upload_video(): 上传本地视频一次，取得 oss:// 引用。
- recommend(): 将调用者提供的 messages 发给 Qwen，返回原始响应。
- extract_reply_text(): 从响应中提取模型回复。
- parse_candidates(): 验证并提取候选时间点和推荐说明。

本模块不管理对话历史、推荐轮次、审批状态或磁盘留痕。
"""

import json
import math
import mimetypes
from pathlib import Path
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from lia_agent.config import (
    DASHSCOPE_BASE_URL,
    DASHSCOPE_API_KEY,
    QWEN_MODEL,
)


MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 保留原型的单视频 100 MiB 限制


def check_base_url(value: str) -> str:
    """校验百炼北京业务空间的 DashScope API 地址。"""
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    valid_host = hostname == "dashscope.aliyuncs.com" or bool(
        re.fullmatch(r"ws-[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com", hostname)
    )
    if (
        parsed.scheme != "https"
        or not valid_host
        or parsed.path != "/api/v1"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port
    ):
        raise ValueError("请输入北京业务空间的 DashScope HTTPS 地址，以 /api/v1 结尾。")
    return value


def _check_http(response) -> None:
    """检查 HTTP 错误，避免把上传凭证或响应原文写进报错。"""
    if response.status_code == 200:
        return
    try:
        data = response.json()
    except ValueError:
        try:
            root = ElementTree.fromstring(response.text)
            data = {
                "code": root.findtext("Code"),
                "message": root.findtext("Message"),
                "request_id": root.findtext("RequestId"),
            }
        except ElementTree.ParseError:
            data = {"message": "响应不是预期的 JSON/XML 错误内容"}
    if not isinstance(data, dict):
        data = {"message": "非预期的 HTTP 错误响应"}
    raise RuntimeError(
        f"HTTP {response.status_code} | code={data.get('code', '')} | "
        f"message={data.get('message', '')} | request_id={data.get('request_id', '')}"
    )


class QwenClient:
    """只负责视频上传和模型 API 请求；历史消息由 Workflow 提供。"""

    def __init__(
        self,
        base_url: str = DASHSCOPE_BASE_URL,
        api_key: str = DASHSCOPE_API_KEY,
    ):
        if not api_key or not api_key.strip():
            raise ValueError("缺少 DashScope API Key")
        self.base_url = check_base_url(base_url)
        self.api_key = api_key.strip()
        try:
            import dashscope
            import requests
        except ImportError as exc:
            raise RuntimeError("缺少 dashscope 或 requests，请检查 lia_content_agent 环境。") from exc
        self._sdk = dashscope
        self._http = requests
        # 与之前的已测原型保持一致。当前应用为单用户单业务空间。
        self._sdk.base_http_api_url = self.base_url

    def upload_video(self, video_path: str | Path) -> str:
        """上传本地视频，返回云端 oss:// 引用；后续轮次不必重复上传。"""
        video = Path(video_path).expanduser()
        if not video.is_file() or video.stat().st_size == 0:
            raise ValueError("视频文件不存在或为空")
        if video.stat().st_size > MAX_VIDEO_BYTES:
            raise ValueError("当前视频超过 100 MiB 的测试限制")

        # 1. 获取百炼上传凭证
        response = self._http.get(
            self.base_url + "/uploads",
            headers={"Authorization": "Bearer " + self.api_key},
            params={"action": "getPolicy", "model": QWEN_MODEL},
            timeout=(15, 60),
            allow_redirects=False,
        )
        _check_http(response)
        policy = response.json()["data"]
        upload_host = policy["upload_host"]
        parsed = urlsplit(upload_host)
        if (
            parsed.scheme != "https"
            or not (parsed.hostname or "").endswith(".aliyuncs.com")
            or parsed.username
            or parsed.password
        ):
            raise RuntimeError("上传凭证中的上传地址不是预期的阿里云 HTTPS 地址")

        object_key = policy["upload_dir"].rstrip("/") + "/" + video.name
        form = {
            "OSSAccessKeyId": policy["oss_access_key_id"],
            "Signature": policy["signature"],
            "policy": policy["policy"],
            "key": object_key,
            "x-oss-object-acl": policy["x_oss_object_acl"],
            "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
            "success_action_status": "200",
        }
        # 2. 上传原视频
        with video.open("rb") as stream:
            response = self._http.post(
                upload_host,
                data=form,
                files={
                    "file": (
                        video.name,
                        stream,
                        mimetypes.guess_type(video.name)[0] or "application/octet-stream",
                    )
                },
                timeout=(15, 180),
                allow_redirects=False,
            )
        _check_http(response)
        return "oss://" + object_key

    def recommend(self, messages: list[dict]) -> dict:
        """执行一轮 Qwen 请求；调用者负责组织并追加 messages。"""
        response = self._sdk.MultiModalConversation.call(
            model=QWEN_MODEL,
            api_key=self.api_key,
            messages=messages,
            enable_thinking=False,
            max_tokens=4096,
            result_format="message",
            stream=False,
            request_timeout=180,
        )
        return dict(response)


def extract_reply_text(payload: dict) -> str:
    """从一次 Qwen 响应提取完整文字，非法响应抛错。"""
    if payload.get("status_code") != 200:
        raise RuntimeError(
            f"Qwen 返回错误：HTTP {payload.get('status_code')} | "
            f"code={payload.get('code')} | request_id={payload.get('request_id')}"
        )
    try:
        choice = payload["output"]["choices"][0]
        content = choice["message"]["content"]
        text = "\n".join(
            part["text"] for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        if choice.get("finish_reason") != "stop" or not text.strip():
            raise ValueError("模型回复不完整或为空")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"无法提取模型回复：{exc}") from exc
    return text


def parse_candidates(reply_text: str, video_duration: float) -> dict:
    """验证候选 JSON，保留原型中允许小数秒的时间点规则。"""
    clean = reply_text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    data = json.loads(clean)
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise ValueError("缺少 candidates 数组")
    candidates = data["candidates"]
    if len(candidates) > 3:
        raise ValueError("候选数量不能超过 3")
    notes = data.get("notes", "")
    if not isinstance(notes, str) or (len(candidates) < 3 and not notes.strip()):
        raise ValueError("候选不足 3 张时需在 notes 中说明")

    times, ranks = set(), set()
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("候选条目必须为对象")
        seconds, rank = item.get("time_seconds"), item.get("rank")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 <= seconds < video_duration
            or seconds in times
        ):
            raise ValueError("候选时间点必须互不重复且落在视频时长内")
        if type(rank) is not int or not 1 <= rank <= 3 or rank in ranks:
            raise ValueError("rank 必须是互不重复的 1～3 整数")
        if any(not isinstance(item.get(k), str) or not item[k].strip() for k in ("shot", "reason")):
            raise ValueError("每个候选必须有 shot 和 reason")
        issues = item.get("potential_issues")
        if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
            raise ValueError("potential_issues 必须是字符串数组")
        times.add(seconds)
        ranks.add(rank)

    return {"candidates": sorted(candidates, key=lambda x: x["rank"]), "notes": notes}
