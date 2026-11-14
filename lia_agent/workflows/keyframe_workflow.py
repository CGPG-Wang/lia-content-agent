"""候选帧推荐 Workflow v0.1：协调 Qwen、FFmpeg 和 Storage。

提供 start_recommendation() 和 continue_recommendation() 两个业务入口。
一段连续对话只上传一次视频；每次推荐都是单独的 Qwen 请求，携带全部历史。

本版不做 跨进程恢复 或 失败后的自动重试。
"""

from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import math
import shutil
from lia_agent.storage.keyframe_store import save_selection
from lia_agent.config import KEYFRAME_PROMPT, VIDEO_FPS
from lia_agent.services.frame_extractor import extract_candidate_frames, probe_video_frames
from lia_agent.services.qwen_client import QwenClient, extract_reply_text, parse_candidates
from lia_agent.storage.keyframe_store import (
    create_round_dir,
    ensure_keyframe_root,
    save_round_candidates,
    save_round_feedback,
    save_round_reply,
    load_recovery_task,
    update_keyframe_state,
)


@dataclass
class KeyframeSession:
    """当前多轮对话在内存中的信息；后续可保存在 st.session_state。"""

    task_dir: Path
    video_path: Path
    video_uri: str
    video_duration: float
    messages: list[dict[str, Any]]
    current_round: int = 0
    failed: bool = False


@dataclass(frozen=True)
class RoundResult:
    """单轮执行结果，供今后的 UI 展示。"""

    round_number: int
    round_dir: Path
    candidates: list[dict]
    notes: str
    reply_text: str
    status: str


def _read_saved_inputs(task_dir: str | Path) -> tuple[Path, Path, str]:
    """从已创建任务的 inputs.json 找视频与附加要求。

    当前暂时在 Workflow 读取；后面增加 Storage 读取接口时下沉。
    """
    root = Path(task_dir).expanduser().resolve()
    records = ensure_keyframe_root(root)
    inputs_file = records / "inputs.json"
    if not inputs_file.is_file():
        raise FileNotFoundError(f"找不到任务输入记录：{inputs_file}")
    data = json.loads(inputs_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("inputs.json 必须是 JSON 对象。")

    video_name = data.get("saved_video_name")
    if not isinstance(video_name, str) or not video_name or Path(video_name).name != video_name:
        raise ValueError("inputs.json 中的 saved_video_name 不合法。")
    video = root / video_name
    if not video.is_file() or video.stat().st_size == 0:
        raise FileNotFoundError(f"找不到已保存的视频，或文件为空：{video}")
    extra = data.get("extra_requirements", "")
    if not isinstance(extra, str):
        raise ValueError("inputs.json 中的 extra_requirements 必须是字符串。")
    return root, video, extra


def _run_round(session: KeyframeSession, client: QwenClient) -> RoundResult:
    """一轮推荐：Qwen 回复 → 留原文 → FFmpeg 抽帧 → 留完整 JSON。"""
    if session.failed:
        raise RuntimeError("本次对话曾发生错误，当前版本不支持自动续跑或重试。")

    number, round_dir = create_round_dir(session.task_dir)
    if number != session.current_round + 1:
        session.failed = True
        raise RuntimeError("磁盘轮次与内存轮次不一致，已停止以避免重复请求。")

    try:
        payload = client.recommend(session.messages)
        reply_text = extract_reply_text(payload)
        save_round_reply(round_dir, reply_text)

        # 与已测试的旧版命令行脚本一致：格式不合法也保留回复并允许用户反馈修正。
        try:
            parsed = parse_candidates(reply_text, session.video_duration)
        except (ValueError, TypeError) as exc:
            candidates = []
            notes = f"Qwen 候选格式未通过校验：{exc}。原始回复已保存，请反馈要求修正。"
            status = "awaiting_decision"
        else:
            if parsed["candidates"]:
                candidates = extract_candidate_frames(
                    video_path=session.video_path,
                    candidates=parsed["candidates"],
                    output_dir=round_dir,
                )
                status = "awaiting_decision"
            else:
                # Qwen 判断无适合画面时，不调用 FFmpeg，允许反馈下一轮。
                candidates = []
                status = "awaiting_decision"
            notes = parsed["notes"]
            save_round_candidates(round_dir, {
                **parsed,
                "source_video": str(session.video_path),
                "candidates": candidates,
            })

        # 本轮 Qwen 返回已处理并完成必要留痕
        # 无论是否有有效候选，都进入统一的等待决策状态
        update_keyframe_state(
            task_dir=session.task_dir,
            stage="awaiting_decision",
            current_round=number,
        )

    except Exception:
        # 保留已经产生的部分文件，不静默重新请求 Qwen。
        session.failed = True
        raise

    session.messages.append({"role": "assistant", "content": [{"text": reply_text}]})
    session.current_round = number
    return RoundResult(
        round_number=number,
        round_dir=round_dir,
        candidates=candidates,
        notes=notes,
        reply_text=reply_text,
        status=status,
    )


def start_recommendation(
    task_dir: str | Path,
    *,
    client: QwenClient | None = None,
) -> tuple[KeyframeSession, RoundResult]:
    """新建会话并执行第一轮，返回 (KeyframeSession, RoundResult)。

    只能用于通过 task_store 创建、还没有任何推荐轮次的新任务。
    """
    root, video_path, extra = _read_saved_inputs(task_dir)
    records = ensure_keyframe_root(root)
    if any(p.is_dir() and p.name.startswith("round_") for p in records.iterdir()):
        raise FileExistsError("此任务已有推荐轮次，当前版本不支持在原目录重新开启对话。")

    # 在调用云端 API 之前先检查本地视频有效性。
    frames, duration = probe_video_frames(video_path)
    if not frames or duration is None or duration < 2:
        raise ValueError("视频时长必须能读取，且至少为 2 秒。")

    qwen = client if client is not None else QwenClient()
    video_uri = qwen.upload_video(video_path)  # 仅此处上传一次
    request_text = "请按筛选规则，从上传的视频中推荐3张画面。"
    if extra.strip():
        request_text += "\n额外要求：" + extra.strip()

    session = KeyframeSession(
        task_dir=root,
        video_path=video_path,
        video_uri=video_uri,
        video_duration=duration,
        messages=[
            {"role": "system", "content": [{"text": KEYFRAME_PROMPT}]},
            {"role": "user", "content": [
                {"video": video_uri, "fps": VIDEO_FPS},
                {"text": request_text},
            ]},
        ],
    )
    return session, _run_round(session, qwen)


def continue_recommendation(
    session: KeyframeSession,
    feedback: str,
    *,
    client: QwenClient | None = None,
) -> RoundResult:
    """先留痕用户反馈，再携带之前全部 messages 请求下一轮。"""
    if session.failed:
        raise RuntimeError("本次会话曾发生错误，拒绝重复发送 API 请求。")
    if session.current_round < 1:
        raise ValueError("必须先完成第一轮推荐。")
    if not isinstance(feedback, str):
        raise ValueError("反馈必须是字符串。")

    # 用户允许不填写额外反馈
    # 空反馈时，系统自动构造下一轮推荐要求
    effective_feedback = feedback.strip()

    if not effective_feedback:
        effective_feedback = (
            "用户没有提供额外修改意见。"
            "请继续根据原视频和既定筛选规则推荐候选关键帧，"
            "尽量选择与历史推荐不同的时间点和人物状态。"
            "如果没有更合适的画面，请如实说明。"
        )

    last_round = (
        ensure_keyframe_root(session.task_dir)
        / f"round_{session.current_round:02d}"
    )
    # 格式不合格时只有 reply.txt，仍允许提交反馈让 Qwen 修正。
    if not (last_round / "reply.txt").is_file():
        raise RuntimeError("上一轮缺少 Qwen 回复记录，不能继续推荐。")

    # 保存本轮实际使用的反馈内容
    save_round_feedback(last_round, effective_feedback)

    # 将反馈追加到当前 Qwen 对话历史
    session.messages.append({
        "role": "user",
        "content": [{"text": effective_feedback}]
    })


    qwen = client if client is not None else QwenClient()
    return _run_round(session, qwen)


def approve_candidate(
    task_dir: str | Path,
    round_number: int,
    rank: int,
) -> Path:
    """确认一张候选帧，生成 1f-original.png 并保存选择记录。

    当前版本只支持首次确认，不覆盖已有审批。
    """

    # 1. 验证用户提交的轮次和候选编号
    if type(round_number) is not int or round_number < 1:
        raise ValueError("轮次编号不合法。")

    if type(rank) is not int or rank < 1:
        raise ValueError("候选编号不合法。")

    root = Path(task_dir).expanduser().resolve()
    records = ensure_keyframe_root(root)

    round_dir = records / f"round_{round_number:02d}"
    candidates_file = round_dir / "candidates.json"

    if not candidates_file.is_file():
        raise FileNotFoundError("找不到该轮候选帧记录。")

    # 2. 从本地留痕读取经过验证的候选数据
    document = json.loads(
        candidates_file.read_text(encoding="utf-8")
    )

    candidates = document.get("candidates", [])

    selected = next(
        (item for item in candidates if item.get("rank") == rank),
        None,
    )

    if selected is None:
        raise ValueError("该轮不存在指定的候选帧。")

    # 3. 验证对应的 PNG 图片
    image_name = selected.get("image_file")

    if (
        not isinstance(image_name, str)
        or not image_name
        or Path(image_name).name != image_name
        or Path(image_name).suffix.lower() != ".png"
    ):
        raise ValueError("候选图片文件名不合法。")

    source = (round_dir / image_name).resolve()

    if source.parent != round_dir.resolve() or not source.is_file():
        raise FileNotFoundError("候选图片不存在或路径异常。")

    seconds = selected.get("time_seconds")

    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds < 0
    ):
        raise ValueError("候选时间点不合法。")

    # 4. 首次审批禁止覆盖已有文件
    destination = root / "1f-original.png"
    selection_file = records / "selection.json"

    if destination.exists() or selection_file.exists():
        raise FileExistsError(
            "当前任务已有确认结果，暂不支持覆盖。"
        )

    # 5. 复制选中的 PNG 到生产任务根目录
    with source.open("rb") as original:
        with destination.open("xb") as output:
            shutil.copyfileobj(original, output)

    # 6. 通过 Storage 保存审批信息
    selection_data = {
        "selected_round": round_number,
        "selected_rank": rank,
        "selected_image": f"round_{round_number:02d}/{image_name}",
        "selected_time_seconds": seconds,
    }

    try:
        save_selection(root, selection_data)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    # 关键帧已经确认，更新任务状态
    update_keyframe_state(
        task_dir=root,
        stage="candidate_approved",
        current_round=round_number,
        selected_candidate=selection_data,
    )

    return destination


def replace_candidate_final(
    task_dir: str | Path,
    round_number: int,
    rank: int,
) -> Path:
    """恢复任务时最后一次替换关键帧，保留原图和两次选择记录。"""

    # 1. 检查候选编号
    if type(round_number) is not int or round_number < 1:
        raise ValueError("轮次编号不合法。")

    if type(rank) is not int or rank < 1:
        raise ValueError("候选编号不合法。")

    # 2. 读取恢复任务的数据
    recovery = load_recovery_task(task_dir)

    root = recovery["task_dir"]
    state = recovery["state"]
    old_selection = recovery["selection"]
    current_image = recovery["approved_image"]
    history = recovery["history"]

    records = root / "workspace" / "candidates_recomm"
    selection_file = records / "selection.json"

    # 新版关键帧备份文件
    backup_image = root / "1f-original-bk.png"

    # 兼容此前已经产生的旧版备份
    legacy_backup_image = root / "1f-original.png.bk"

    # 3. 必须已经完成首次审批
    if old_selection is None or current_image is None:
        raise ValueError("当前任务尚未完成首次关键帧审批。")

    if state is None:
        raise FileNotFoundError("找不到 state.json，不能执行最终替换。")

    # 4. 禁止第三次选择
    if (
            state.get("stage") == "candidate_replaced_final"
            or old_selection.get("final_replacement") is True
            or "selection_history" in old_selection
            or backup_image.exists()
            or legacy_backup_image.exists()
    ):
        raise ValueError(
            "当前任务已经执行过最终替换，不能再次修改。"
        )

    # 不允许把原来的候选再次选为新候选
    if (
        old_selection.get("selected_round") == round_number
        and old_selection.get("selected_rank") == rank
    ):
        raise ValueError("新选择与当前关键帧相同，无需替换。")

    # 5. 找到用户选中的历史候选帧
    target_round = next(
        (
            item for item in history
            if item["round_number"] == round_number
        ),
        None,
    )

    if target_round is None:
        raise ValueError("找不到指定的历史推荐轮次。")

    candidate = next(
        (
            item for item in target_round["candidates"]
            if item["rank"] == rank
        ),
        None,
    )

    if candidate is None:
        raise ValueError("找不到指定的历史候选帧。")

    image_name = candidate["image_file"]
    round_dir = target_round["round_dir"].resolve()
    source_image = (round_dir / image_name).resolve()

    if (
        source_image.parent != round_dir
        or not source_image.is_file()
    ):
        raise FileNotFoundError("候选图片不存在或路径不合法。")

    seconds = candidate.get("time_seconds")

    if (
        type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or seconds < 0
    ):
        raise ValueError("候选帧时间点不合法。")

    # 6. 生成最终选择记录
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    offset_text = (
        f"{offset[:3]}:{offset[3:]}".removeprefix("+")
    )

    confirmed_at = (
        f"{now:%Y-%m-%d}｜{now:%H:%M:%S}｜{offset_text}"
    )

    new_choice = {
        "selected_round": round_number,
        "selected_rank": rank,
        "selected_image": f"round_{round_number:02d}/{image_name}",
        "selected_time_seconds": seconds,
    }

    original_choice = {
        "selected_round": old_selection["selected_round"],
        "selected_rank": old_selection["selected_rank"],
        "selected_image": old_selection["selected_image"],
        "selected_time_seconds": old_selection["selected_time_seconds"],
        "confirmed_at": old_selection.get("confirmed_at"),
    }

    final_selection = {
        **old_selection,
        **new_choice,
        "confirmed_at": confirmed_at,
        "final_replacement": True,
        "selection_history": [
            original_choice,
            {
                **new_choice,
                "confirmed_at": confirmed_at,
            },
        ],
    }

    # 7. 提前准备新文件，避免直接覆盖原图
    pending_image = root / "1f-original.png.new"
    pending_selection = records / "selection.json.new"

    if pending_image.exists() or pending_selection.exists():
        raise FileExistsError(
            "发现上次操作遗留的临时文件，请先检查任务目录。"
        )

    # 保留原 selection.json，用于异常时恢复
    original_selection_text = selection_file.read_text(
        encoding="utf-8"
    )

    backup_created = False
    selection_updated = False

    try:
        # 先复制候选 PNG 到临时文件
        with source_image.open("rb") as src:
            with pending_image.open("xb") as dst:
                shutil.copyfileobj(src, dst)

        # 提前准备新的审批 JSON
        with pending_selection.open("x", encoding="utf-8") as file:
            json.dump(
                final_selection,
                file,
                ensure_ascii=False,
                indent=2,
            )

        # 8. 将原来的关键帧改名为 .bk
        current_image.replace(backup_image)
        backup_created = True

        # 9. 新图片成为当前 1f-original.png
        pending_image.replace(current_image)

        # 10. 更新 selection.json
        pending_selection.replace(selection_file)
        selection_updated = True

        # 11. 最后更新任务状态
        latest_round = max(
            [item["round_number"] for item in history]
            + [round_number]
        )

        update_keyframe_state(
            task_dir=root,
            stage="candidate_replaced_final",
            current_round=latest_round,
            selected_candidate=new_choice,
        )

    except Exception:
        # 普通执行异常时，尽量还原首次确认结果
        if backup_created and backup_image.is_file():
            current_image.unlink(missing_ok=True)
            backup_image.replace(current_image)

        if selection_updated:
            selection_file.write_text(
                original_selection_text,
                encoding="utf-8",
            )

        raise

    finally:
        # 清理本次操作产生的临时文件
        pending_image.unlink(missing_ok=True)
        pending_selection.unlink(missing_ok=True)

    return current_image
