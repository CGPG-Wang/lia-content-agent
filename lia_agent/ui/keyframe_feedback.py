
"""候选帧反馈页：浏览历史候选，提交补充要求，并确认关键帧。

支持继续多轮 Qwen 推荐，并保留已有轮次供人工审核。
依赖 keyframe_store.load_round_history() 和 keyframe_workflow.approve_candidate()。
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from lia_agent.storage.keyframe_store import load_round_history
from lia_agent.workflows.keyframe_workflow import approve_candidate


@st.dialog("确认使用这张候选帧？")
def _confirm_candidate(
    task_dir: str | Path,
    round_number: int,
    rank: int,
    image_path: Path,
) -> None:
    """只有用户点击 OK 才真正写入选择记录。"""

    st.write(f"第 {round_number} 轮 · 候选 {rank}")
    st.caption("确认后将保存为生产目录下的 1f-original.png。")

    if image_path.is_file():
        st.image(str(image_path), use_container_width=True)
    else:
        st.error("候选图片已经不存在，无法确认。")

        if st.button("Cancel", key="feedback_dialog_missing_cancel"):
            st.rerun()

        return

    cancel_col, confirm_col = st.columns(2)

    with cancel_col:
        if st.button(
            "Cancel",
            use_container_width=True,
            key="feedback_dialog_cancel",
        ):
            # 关闭弹窗，不保存任何内容
            st.rerun()

    with confirm_col:
        if st.button(
            "OK",
            type="primary",
            use_container_width=True,
            key="feedback_dialog_ok",
        ):
            try:
                approve_candidate(
                    task_dir,
                    round_number,
                    rank,
                )

            except (
                ValueError,
                FileNotFoundError,
                FileExistsError,
                OSError,
            ) as exc:
                st.error(f"确认失败：{exc}")

            else:
                # 将弹窗中的成功事件传递给 app.py
                st.session_state["feedback_approved_task"] = str(
                    Path(task_dir).expanduser().resolve()
                )

                st.rerun()


def render_keyframe_feedback(
    task_dir: str | Path,
) -> bool | tuple[str, str]:
    """渲染双栏反馈页。选帧成功返回 True，否则返回 False。"""

    root = Path(task_dir).expanduser().resolve()
    pending_feedback = st.session_state.get("keyframe_feedback_pending", None)
    is_requesting_qwen = pending_feedback is not None

    # 本次是否提交了新一轮推荐
    # None 表示没有提交，空字符串表示用户没有填写额外反馈
    request_feedback: str | None = None

    # 弹窗确认成功后，向 app.py 返回成功状态
    if st.session_state.get("feedback_approved_task") == str(root):

        del st.session_state["feedback_approved_task"]

        return True

    st.title("候选帧反馈")
    st.caption(f"当前生产任务目录：{root}")

    # 左右两栏
    left, right = st.columns([1, 1], gap="large")

    # ==========================================
    # 左侧：系统消息及用户反馈
    # ==========================================

    with left:
        st.subheader("系统消息与补充要求")

        st.info(
            "如果已有满意的关键帧，"
            "可直接在右侧历史推荐中选择。"
        )

        st.write(
            "继续进行下一轮 Qwen 推荐。"
        )

        # 用户输入额外的修改意见，允许为空
        if is_requesting_qwen:
            st.warning("正在请求服务器，请不要重复点击。")

        feedback = st.text_area(
            "补充要求（可留空）",
            placeholder="例如：希望人物正脸更清晰，尽量推荐不同的画面",
            key="keyframe_feedback_input",
            height=150,
            disabled=is_requesting_qwen,
        )

        if st.button(
                "正在请求 Qwen…" if is_requesting_qwen else "再次请求 Qwen",
                type="primary",
                key="keyframe_feedback_submit",
                disabled=is_requesting_qwen,
        ):
            st.session_state.keyframe_feedback_pending = feedback
            st.rerun()


    # ==========================================
    # 右侧：历史 Qwen 推荐结果
    # ==========================================

    with right:
        st.subheader("历史推荐")

        try:
            history = load_round_history(root)

        except (OSError, ValueError) as exc:
            st.error(f"读取历史推荐失败：{exc}")
            return False

        if not history:
            st.info("还没有可以展示的历史推荐。")

        # 最新轮次排在最上方
        for record in reversed(history):

            number = record["round_number"]
            candidates = record["candidates"]

            # 每一轮都是一个可以展开/收起的区域
            with st.expander(
                f"第 {number} 轮 · {len(candidates)} 张候选帧",
                expanded=(
                    number == history[-1]["round_number"]
                ),
            ):

                if record.get("notes"):
                    st.caption(str(record["notes"]))

                if not candidates:
                    st.info("本轮没有可供选择的完整候选帧。")

                # 展示本轮所有候选帧

                # 每轮创建 3 列，候选图片并排展示
                candidate_columns = st.columns(3, gap="small")

                for index, candidate in enumerate(candidates):

                    rank = candidate["rank"]
                    image_name = candidate["image_file"]

                    image_path = record["round_dir"] / image_name

                    # 将三张图片分配到三列
                    with candidate_columns[index % 3]:

                        st.markdown(f"**候选 {rank}**")

                        # 缩略图：只填充当前小列
                        st.image(
                            str(image_path),
                            use_container_width=True,
                        )

                        seconds = candidate.get("time_seconds")

                        if isinstance(seconds, (int, float)):
                            st.caption(f"{seconds:.3f} 秒")

                        # 只展示简短的推荐理由，避免撑高列表
                        reason = candidate.get("reason") or "未提供推荐理由"

                        with st.popover("推荐详情", use_container_width=True):
                            st.write(f"推荐理由：{reason}")

                            risks = candidate.get("potential_issues", [])

                            if isinstance(risks, list):
                                st.write(
                                    "潜在问题："
                                    + ("；".join(map(str, risks)) or "未列出")
                                )
                            else:
                                st.write(f"潜在问题：{risks}")

                        # 点击后打开原有确认弹窗
                        if st.button(
                                "选择这张",
                                key=f"feedback_choose_{number}_{rank}",
                                use_container_width=True,
                        ):
                            _confirm_candidate(
                                root,
                                number,
                                rank,
                                image_path,
                            )

    # 用户点击了再次请求服务器
    if is_requesting_qwen:
        return ("continue", pending_feedback)

    return False
