"""从本地视频提取 Qwen 推荐的候选帧；不调用模型，也不写业务 JSON。"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path


def _run_command(args: list[str], timeout: int = 180) -> str:
    """调用本地视频工具。任何非零退出码都视为失败。"""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"本地视频工具执行超时（{timeout} 秒）。") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-1600:] or "本地视频工具执行失败。")
    return result.stdout


def _video_file(video_path: str | Path) -> Path:
    video = Path(video_path).expanduser()
    if not video.is_file() or video.stat().st_size == 0:
        raise ValueError(f"视频文件不存在或为空：{video}")
    return video.resolve()


def probe_video_frames(video_path: str | Path) -> tuple[list[tuple[int, float]], float | None]:
    """返回 [(实际解码帧序号, 相对视频起点的秒数), ...] 及视频时长。"""
    video = _video_file(video_path)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("没有找到 ffprobe，请检查当前 Conda 环境中的 FFmpeg。")

    output = _run_command([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp_time:stream=duration:format=duration",
        "-of", "json", str(video),
    ])
    try:
        info = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe 没有返回有效的 JSON 数据。") from exc

    frames = []
    for index, frame in enumerate(info.get("frames", [])):
        try:
            timestamp = float(frame["best_effort_timestamp_time"])
            if math.isfinite(timestamp):
                frames.append((index, timestamp))
        except (KeyError, TypeError, ValueError):
            continue
    if not frames:
        raise ValueError("视频没有可用的帧时间戳。")

    # 与旧版原型一致：以实际第一帧作为视频的 0 秒。
    first_timestamp = frames[0][1]
    relative_frames = [(index, timestamp - first_timestamp) for index, timestamp in frames]

    stream = (info.get("streams") or [{}])[0]
    duration = None
    for value in (stream.get("duration"), info.get("format", {}).get("duration")):
        try:
            number = float(value)
            if math.isfinite(number) and number > 0:
                duration = number
                break
        except (TypeError, ValueError):
            continue
    return relative_frames, duration


def extract_candidate_frames(
    video_path: str | Path,
    candidates: list[dict],
    output_dir: str | Path,
) -> list[dict]:
    """在已存在的目录中导出 PNG，返回包含图片文件名、实际帧时间的候选数据。

    output_dir 由调用者（将来的 Workflow / Storage）准备；本函数只生成图片，
    不创建 round_XX，不保存 candidates.json，不处理人工选择或审批。
    """
    video = _video_file(video_path)
    destination = Path(output_dir).expanduser()
    if not destination.is_dir():
        raise ValueError("输出目录不存在，请先由 Storage 创建轮次目录。")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 3:
        raise ValueError("候选帧数量必须为 1 到 3 个。")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        raise RuntimeError("没有找到 ffmpeg / ffprobe，请检查 Conda 环境。")

    frames, duration = probe_video_frames(video)
    planned = []
    used_ranks, used_frames = set(), set()
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("每个候选必须是字典对象。")
        rank, seconds = item.get("rank"), item.get("time_seconds")
        if type(rank) is not int or not 1 <= rank <= 3 or rank in used_ranks:
            raise ValueError("候选 rank 必须是互不重复的 1、2 或 3。")
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds < 0):
            raise ValueError("候选时间点必须是有效的非负数字（秒）。")
        if duration is not None and seconds >= duration:
            raise ValueError(f"候选时间 {seconds} 秒超出视频时长 {duration:.3f} 秒。")
        frame_index, actual_seconds = min(frames, key=lambda x: abs(x[1] - seconds))
        if frame_index in used_frames:
            raise ValueError("两个候选时间点落在同一实际视频帧上，无法生成不同的图片。")
        filename = f"candidate_{rank:02d}.png"
        target = destination / filename
        if target.exists():
            raise FileExistsError(f"已有候选图片，拒绝覆盖：{target}")
        used_ranks.add(rank)
        used_frames.add(frame_index)
        planned.append((item, frame_index, actual_seconds, filename, target))

    # 临时目录只用于本次抽帧；失败不会留下不完整的 PNG。
    with tempfile.TemporaryDirectory(prefix=".extracting_", dir=destination) as tmp:
        staging = Path(tmp)
        for _item, frame_index, _actual_seconds, filename, _target in planned:
            staged_image = staging / filename
            _run_command([
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
                "-i", str(video), "-map", "0:v:0",
                "-vf", f"select=eq(n\\,{frame_index})",
                "-frames:v", "1", "-fps_mode", "passthrough", "-update", "1",
                str(staged_image),
            ])
            if not staged_image.is_file() or staged_image.stat().st_size == 0:
                raise RuntimeError(f"无法提取候选帧：{filename}")

        created = []
        try:
            # xb 独占创建，防止意外覆盖已有文件。
            for _item, _index, _actual, filename, target in planned:
                with (staging / filename).open("rb") as source, target.open("xb") as dest:
                    created.append(target)
                    shutil.copyfileobj(source, dest)
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise

    # 不修改原始 candidates，交给调用者决定如何保存增强后的结果。
    return [
        {**item, "image_file": filename,
         "actual_time_seconds": actual_seconds, "frame_index": frame_index}
        for item, frame_index, actual_seconds, filename, _target in planned
    ]
