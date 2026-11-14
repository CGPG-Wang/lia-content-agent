
"""候选帧审核页面：展示 Workflow 返回的本轮推荐结果。"""

from pathlib import Path
import streamlit as st
from lia_agent.workflows.keyframe_workflow import (
    RoundResult,
    approve_candidate,
)


def render_keyframe_review(task_dir, result):
    """展示本轮候选帧、推荐原因及潜在问题。"""

    task_root = Path(task_dir)

    already_approved = (
            (task_root / "1f-original.png").exists()
            or (
                    task_root
                    / "workspace"
                    / "candidates_recomm"
                    / "selection.json"
            ).exists()
    )

    if already_approved:
        return True

    st.title("候选帧审核")
    st.caption(f"当前生产任务目录：{task_dir}")

    st.subheader(f"第 {result.round_number} 轮候选帧推荐")

    # 没有候选帧时，展示 Workflow 返回的说明
    if not result.candidates:
        st.warning("本轮没有可供选择的候选帧。")
        if result.notes:
            st.write(result.notes)
        return

    # 一行展示三张候选帧
    columns = st.columns(3, gap="large")

    for index, candidate in enumerate(result.candidates):
        rank = candidate["rank"]

        with columns[index % 3]:

            st.markdown(
                f"### 候选 {candidate['rank']}"
            )

            # 从当前轮次目录加载 FFmpeg 导出的图片
            image_path = (
                Path(result.round_dir)
                / candidate["image_file"]
            )

            if image_path.is_file():
                st.image(
                    str(image_path),
                    use_container_width=True,
                )
            else:
                st.error(f"找不到候选图片：{image_path.name}")

            st.write(
                f"**推荐时间：** {candidate['time_seconds']:.3f} 秒"
            )

            st.write(
                f"**实际取帧：** {candidate['actual_time_seconds']:.3f} 秒"
            )

            st.write(
                f"**镜头描述：** {candidate['shot']}"
            )

            st.write(
                f"**推荐理由：** {candidate['reason']}"
            )

            issues = candidate.get("potential_issues", [])

            st.write(
                "**潜在问题：**",
                "；".join(issues) if issues else "未列出"
            )

            # 确认当前候选帧
            if st.button(
                    f"确认候选 {rank}",
                    key=f"approve_{result.round_number}_{rank}",
                    disabled=already_approved,
                    type="primary",
            ):
                try:
                    saved_image = approve_candidate(
                        task_dir=task_dir,
                        round_number=result.round_number,
                        rank=rank,
                    )

                except (ValueError, FileNotFoundError, FileExistsError, OSError) as exc:
                    st.error(f"确认失败：{exc}")

                else:
                    return True

    # 本轮整体说明
    if result.notes:
        st.divider()
        st.subheader("本轮推荐说明")
        st.write(result.notes)

    st.divider()

    if st.button(
            "都不满意，查看历史候选帧",
            key="go_to_feedback",
    ):
        return "feedback"
