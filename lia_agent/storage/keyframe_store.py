"""候选帧推荐模块的本地留痕（第一版）。

只负责读写任务目录内的文件；不调用 Qwen、FFmpeg 或 Streamlit。
正式目录：<任务目录>/workspace/candidates_recomm/
"""

import json
from datetime import datetime
from pathlib import Path


def _write_new_text(path: Path, content: str) -> Path:
    """只写入新文件，禁止覆盖已有记录。"""
    if not path.parent.is_dir():
        raise FileNotFoundError(f"记录目录不存在：{path.parent}")
    with path.open("x", encoding="utf-8") as file:
        file.write(content)
    return path


def _write_new_json(path: Path, data: dict) -> Path:
    content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    return _write_new_text(path, content)


def _local_timestamp() -> str:
    """按本项目约定记录 Mac 本地时间，如 2026-10-31｜17:44:33｜08:00。"""
    now = datetime.now().astimezone()
    offset = now.strftime("%z")  # 例如 +0800
    offset_text = f"{offset[:3]}:{offset[3:]}".removeprefix("+")
    return f"{now:%Y-%m-%d}｜{now:%H:%M:%S}｜{offset_text}"


def ensure_keyframe_root(task_dir: str | Path) -> Path:
    """取得候选帧留痕目录；不存在则建立，但不新建生产任务。"""
    task_root = Path(task_dir).expanduser()
    task_file = task_root / "workspace" / "task.json"
    if not task_file.is_file():
        raise FileNotFoundError(
            f"未发现已创建任务的 task.json：{task_file}；请先创建生产任务。"
        )
    keyframe_root = task_root / "workspace" / "candidates_recomm"
    keyframe_root.mkdir(parents=True, exist_ok=True)
    return keyframe_root


def create_round_dir(task_dir: str | Path) -> tuple[int, Path]:
    """创建下一轮独立留痕目录，返回 (轮次编号, 目录路径)。"""
    root = ensure_keyframe_root(task_dir)
    existing_numbers = [
        int(folder.name[6:])
        for folder in root.iterdir()
        if folder.is_dir()
        and folder.name.startswith("round_")
        and folder.name[6:].isdigit()
    ]
    round_number = max(existing_numbers, default=0) + 1
    round_dir = root / f"round_{round_number:02d}"
    round_dir.mkdir(exist_ok=False)
    return round_number, round_dir


def save_round_reply(round_dir: str | Path, reply_text: str) -> Path:
    """保存本轮 Qwen 的原始文字回复。"""
    if not isinstance(reply_text, str) or not reply_text.strip():
        raise ValueError("Qwen 回复必须是非空字符串。")
    return _write_new_text(Path(round_dir) / "reply.txt", reply_text)


def save_round_candidates(round_dir: str | Path, candidates: dict) -> Path:
    """保存本轮完整的结构化候选结果（包含 candidates 数组）。"""
    if not isinstance(candidates, dict) or not isinstance(
        candidates.get("candidates"), list
    ):
        raise ValueError("候选结果必须是包含 candidates 数组的字典。")
    return _write_new_json(Path(round_dir) / "candidates.json", candidates)


def save_round_feedback(round_dir: str | Path, feedback_text: str) -> Path:
    """保存用户对本轮结果的反馈，以便触发下一轮推荐。"""
    if not isinstance(feedback_text, str) or not feedback_text.strip():
        raise ValueError("用户反馈不能为空。")
    return _write_new_text(Path(round_dir) / "user_feedback.txt", feedback_text)


def save_selection(task_dir: str | Path, selection_data: dict) -> Path:
    """保存首次确认的候选帧选择；不会覆盖既有确认记录。"""
    if not isinstance(selection_data, dict):
        raise ValueError("选中结果必须是字典。")
    required = {
        "selected_round", "selected_rank", "selected_image", "selected_time_seconds"
    }
    missing = required - selection_data.keys()
    if missing:
        raise ValueError(f"选中结果缺少字段：{', '.join(sorted(missing))}")
    record = dict(selection_data)
    record.setdefault("confirmed_at", _local_timestamp())
    root = ensure_keyframe_root(task_dir)
    return _write_new_json(root / "selection.json", record)


def update_keyframe_state(
    task_dir: str | Path,
    *,
    stage: str,
    current_round: int,
    selected_candidate: dict | None = None,
) -> Path:
    """更新候选帧推荐任务的 state.json。"""

    # 1. 检查状态是否合法
    allowed_stages = {
        "inputs_saved",
        "awaiting_decision",
        "candidate_approved",
        "candidate_replaced_final",
    }

    if stage not in allowed_stages:
        raise ValueError(f"不支持的任务状态：{stage}")

    if type(current_round) is not int or current_round < 0:
        raise ValueError("当前轮次编号不合法")

    # 2. 找到已经存在的 state.json
    state_file = ensure_keyframe_root(task_dir) / "state.json"

    if not state_file.is_file():
        raise FileNotFoundError(f"找不到任务状态文件：{state_file}")

    # 3. 读取原来的状态
    state = json.loads(
        state_file.read_text(encoding="utf-8")
    )

    if not isinstance(state, dict):
        raise ValueError("state.json 内容不是 JSON 对象")

    previous_round = state.get("current_round", 0)

    if type(previous_round) is not int or previous_round < 0:
        raise ValueError("state.json 中的轮次编号不合法")

    # 4. 更新状态，不清空已有的其他字段
    state["stage"] = stage
    state["current_round"] = max(previous_round, current_round)

    if selected_candidate is not None:
        state["selected_candidate"] = selected_candidate

    # 5. 先写入临时文件，再替换原文件
    temp_file = state_file.with_name("state.json.tmp")

    try:
        temp_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        temp_file.replace(state_file)

    finally:
        temp_file.unlink(missing_ok=True)

    return state_file


def load_round_history(task_dir: str | Path) -> list[dict]:
    """读取历史推荐轮次及实际存在的候选图片，不修改文件。"""

    root = ensure_keyframe_root(task_dir)
    history = []

    for round_dir in root.iterdir():

        # 只处理 round_01、round_02 等轮次目录
        if not (
            round_dir.is_dir()
            and round_dir.name.startswith("round_")
            and round_dir.name[6:].isdigit()
        ):
            continue

        candidates_file = round_dir / "candidates.json"

        if candidates_file.exists():
            data = json.loads(
                candidates_file.read_text(encoding="utf-8")
            )

            if (
                not isinstance(data, dict)
                or not isinstance(data.get("candidates"), list)
            ):
                raise ValueError(
                    f"候选数据格式异常：{candidates_file}"
                )

            raw_candidates = data["candidates"]
            notes = data.get("notes", "")

        else:
            # 只有 reply.txt 的轮次也保留展示入口
            raw_candidates = []
            notes = "本轮没有完整的候选 JSON，无法选帧。"

        candidates = []

        for item in raw_candidates:
            if not isinstance(item, dict):
                continue

            rank = item.get("rank")
            filename = item.get("image_file")

            # 只接受实际存在的候选 PNG
            if (
                type(rank) is not int or rank < 1
                or not isinstance(filename, str)
                or not filename
                or Path(filename).name != filename
                or Path(filename).suffix.lower() != ".png"
            ):
                continue

            if not (round_dir / filename).is_file():
                continue

            candidates.append(item)

        history.append({
            "round_number": int(round_dir.name[6:]),
            "round_dir": round_dir,
            "candidates": candidates,
            "notes": notes,
        })

    history.sort(key=lambda row: row["round_number"])

    return history


def load_recovery_task(task_dir: str | Path) -> dict:
    """读取恢复任务所需的完整数据，不修改已有任务文件。"""

    # 1. 检查用户提供的任务路径
    path = Path(str(task_dir).strip()).expanduser()

    if not path.is_absolute():
        raise ValueError("恢复任务必须提供绝对路径。")

    root = path.resolve()

    task_file = root / "workspace" / "task.json"
    keyframe_root = root / "workspace" / "candidates_recomm"

    if not task_file.is_file():
        raise FileNotFoundError(
            f"找不到任务记录：{task_file}"
        )

    if not keyframe_root.is_dir():
        raise FileNotFoundError(
            f"找不到候选帧记录目录：{keyframe_root}"
        )

    # 2. 读取任务当前状态
    state_file = keyframe_root / "state.json"

    state = None

    if state_file.is_file():
        state = json.loads(
            state_file.read_text(encoding="utf-8")
        )

        if not isinstance(state, dict):
            raise ValueError("state.json 格式不正确。")

    # 3. 读取之前的审批记录
    selection_file = keyframe_root / "selection.json"

    selection = None

    if selection_file.is_file():
        selection = json.loads(
            selection_file.read_text(encoding="utf-8")
        )

        if not isinstance(selection, dict):
            raise ValueError("selection.json 格式不正确。")

    # 4. 检查当前已确认的关键帧是否存在
    image_file = root / "1f-original.png"

    approved_image = (
        image_file if image_file.is_file() else None
    )

    # 5. 复用现有的历史推荐读取函数
    history = load_round_history(root)

    # 6. 将所有恢复信息统一返回
    return {
        "task_dir": root,
        "state": state,
        "selection": selection,
        "approved_image": approved_image,
        "history": history,
    }
