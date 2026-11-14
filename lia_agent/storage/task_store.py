
"""本地生产任务的创建与初始输入保存。"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import BinaryIO


def create_candidate_task(
    task_directory: str,
    scene_id: str,
    video_filename: str,
    video_stream: BinaryIO,
    extra_requirements: str = "",
) -> Path:
    """创建候选帧任务，并保存初始输入。"""

    # 1. 检查输入
    if not task_directory.strip():
        raise ValueError("生产任务目录不能为空")

    video_ext = Path(video_filename).suffix.lower()

    if video_ext not in (".mp4", ".mov", ".m4v"):
        raise ValueError("不支持的视频格式")

    # 去除用户输入路径前后的空白字符
    root = Path(task_directory.strip()).expanduser()

    # 生产任务必须使用绝对路径，避免意外写入项目目录
    if not root.is_absolute():
        raise ValueError("生产任务目录必须是 Mac 本地绝对路径")

    # 规范化路径
    root = root.resolve()

    records = root / "workspace"
    candidate_records = records / "candidates_recomm"

    task_file = records / "task.json"
    inputs_file = candidate_records / "inputs.json"
    state_file = candidate_records / "state.json"

    video_file = root / f"reference-video{video_ext}"

    # 3. 防止覆盖已有任务
    if task_file.exists() or state_file.exists():
        raise FileExistsError("任务已存在，请使用恢复任务功能")

    if list(root.glob("reference-video.*")):
        raise FileExistsError("目录中已有视频参考，请先检查")

    # 4. 创建文件夹
    candidate_records.mkdir(parents=True, exist_ok=True)

    # 5. 复制上传的视频
    video_stream.seek(0)

    with video_file.open("xb") as destination:
        shutil.copyfileobj(video_stream, destination)

    # 6. 保存任务基本信息
    # 获取 Mac 系统本地时间
    now = datetime.now().astimezone()

    # 将时区 +0800 转换为 08:00
    offset = now.strftime("%z")
    timezone_text = f"{offset[:3]}:{offset[3:]}".removeprefix("+")

    # 生成自定义时间格式
    created_at = f"{now:%Y-%m-%d}｜{now:%H:%M:%S}｜{timezone_text}"

    task_data = {
        "schema_version": 1,
        "scene_id": scene_id,
        "created_at": created_at,
    }

    # 7. 保存用户输入
    inputs_data = {
        "original_video_name": video_filename,
        "saved_video_name": video_file.name,
        "extra_requirements": extra_requirements,
    }

    # 8. 初始化任务状态
    state_data = {
        "stage": "inputs_saved",
        "current_round": 0,
        "selected_candidate": None,
    }

    # 9. 将数据写入 JSON 文件
    for file_path, data in (
        (task_file, task_data),
        (inputs_file, inputs_data),
        (state_file, state_data),
    ):
        file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return root
