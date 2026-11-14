
"""候选帧推荐页面：仅负责用户交互和展示。"""

import streamlit as st

from lia_agent.scenes import SCENES
from lia_agent.storage.task_store import create_candidate_task
from pathlib import Path


def render_keyframe_recommendation(scene_id: str) -> Path | None:
    """根据当前创作场景显示候选帧推荐界面。"""

    scene = SCENES[scene_id]

    st.title("候选帧推荐")
    st.caption(f"{scene['model']} · {scene['label']}")

    st.subheader("1. 输入素材")

    video = st.file_uploader(
        "上传视频参考",
        type=["mp4", "mov", "m4v"],
    )

    task_directory = st.text_input(
        "生产任务目录",
        placeholder="/Users/你的用户名/.../2026-11-01_REELS",
    )
    if task_directory.strip():
        root = Path(task_directory.strip()).expanduser().resolve()

        if (root / "workspace" / "task.json").exists():
            st.warning("此目录已有生产任务，请使用恢复已有任务功能。")
        elif root.is_dir():
            st.caption("目标目录已存在；创建任务时将检查是否有冲突文件。")
        else:
            st.caption("目标目录尚不存在；创建任务时将自动建立。")

    extra_requirements = st.text_area(
        "额外推荐要求（可选）",
        placeholder="例如：优先选择正脸清晰、服装完整的画面",
        height=100,
    )

    if video is not None:
        st.subheader("2. 视频参考预览")

        left, right = st.columns([1, 3], gap="large")

        with left:
            st.video(video)

    # 3. 创建任务
    st.subheader("3. 创建候选帧任务")

    if st.button(
            "创建任务并保存素材",
            disabled=video is None or not task_directory.strip(),
            type="primary",
    ):
        try:
            saved_path = create_candidate_task(
                task_directory=task_directory,
                scene_id=scene_id,
                video_filename=video.name,
                video_stream=video,
                extra_requirements=extra_requirements,
            )

        except FileExistsError as exc:
            st.warning(f"任务已存在：{exc}")

        except (ValueError, OSError) as exc:
            st.error(f"创建任务失败：{exc}")

        else:
            return saved_path
