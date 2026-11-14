
"""候选帧任务恢复页面。

支持：
1. 通过绝对路径读取已有生产任务。
2. 查看全部 Qwen 历史候选帧。
3. 尚未审批：首次确认一张历史候选帧。
4. 已首次审批：允许最后一次关键帧替换。
5. 已最终替换：展示当前图片与备份，锁定所有选择操作。
6. 放弃历史结果，返回新建任务流程。

本页面不调用 Qwen 或 FFmpeg。
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from lia_agent.storage.keyframe_store import load_recovery_task
from lia_agent.workflows.keyframe_workflow import (
    approve_candidate,
    replace_candidate_final,
)


# ============================================================
# 确认弹窗：首次审批 / 最终替换
# ============================================================

@st.dialog("确认候选帧操作")
def _confirm_recovery_candidate(
    task_dir: Path,
    round_number: int,
    rank: int,
    image_path: Path,
    mode: str,
) -> None:
    """用户确认后才执行实际文件操作。"""

    if mode not in ("first", "replace"):
        st.error("未知的候选帧操作类型。")
        return

    is_final = mode == "replace"

    st.write(f"第 {round_number} 轮 · 候选 {rank}")

    # 展示即将选择的关键帧
    if not image_path.is_file():
        st.error("候选图片已经不存在，无法继续。")
        return

    st.image(
        str(image_path),
        use_container_width=True,
    )

    # ----------------------------------------------
    # 根据操作类型展示不同提示
    # ----------------------------------------------

    if is_final:

        st.error(
            "**这是当前任务最后一次关键帧替换！**\n\n"
            "确认后：\n\n"
            "- 原 1f-original.png 将备份为 1f-original-bk.png。\n"
            "- 新候选帧将成为 1f-original.png。\n"
            "- 当前任务将被永久锁定，不能再次通过本系统替换。"
        )

        acknowledged = st.checkbox(
            "我理解这是最后一次替换，并确认继续",
            key=f"recovery_ack_{round_number}_{rank}",
        )

    else:

        st.info(
            "确认后将把这张历史候选帧保存为"
            "生产任务目录下的 1f-original.png。"
        )

        acknowledged = True

    # ----------------------------------------------
    # 操作按钮
    # ----------------------------------------------

    cancel_col, confirm_col = st.columns(2)

    with cancel_col:

        if st.button(
            "取消",
            key="recovery_dialog_cancel",
            use_container_width=True,
        ):
            st.rerun()

    with confirm_col:

        if st.button(
            "确认最终替换" if is_final else "确认使用",
            type="primary",
            disabled=not acknowledged,
            key="recovery_dialog_confirm",
            use_container_width=True,
        ):

            try:

                if is_final:

                    saved_path = replace_candidate_final(
                        task_dir=task_dir,
                        round_number=round_number,
                        rank=rank,
                    )

                else:

                    saved_path = approve_candidate(
                        task_dir=task_dir,
                        round_number=round_number,
                        rank=rank,
                    )

            except (
                ValueError,
                FileNotFoundError,
                FileExistsError,
                OSError,
                KeyError,
            ) as exc:

                st.error(f"关键帧操作失败：{exc}")

            else:

                # 使用 session_state 通知主页面操作成功
                st.session_state["recovery_saved_result"] = {
                    "task_dir": str(task_dir),
                    "image_path": str(saved_path),
                    "mode": mode,
                }

                st.rerun()


# ============================================================
# 主页面
# ============================================================

def render_keyframe_recovery() -> str | None:
    """展示恢复页面，返回 new_task 或 None。"""

    st.title("恢复候选帧任务")

    st.caption(
        "输入已有生产任务的绝对路径，"
        "找回历史 Qwen 推荐及之前确认的关键帧。"
    )

    # ========================================================
    # 第一部分：输入任务路径
    # ========================================================


    # ========================================================
    # 生产任务路径输入与动态检测
    # ========================================================

    task_path_input = st.text_input(
        "生产任务绝对路径",
        placeholder="/Users/xxx/.../REELS/1108",
        key="recovery_path_input",
    )

    # 检查当前输入路径是否符合读取条件
    path_ready = False
    checked_path = None

    raw_path = task_path_input.strip()

    if not raw_path:

        st.caption("请输入已有生产任务的绝对路径。")

    else:

        candidate_path = Path(raw_path).expanduser()

        if not candidate_path.is_absolute():

            st.warning("请输入完整的绝对路径。")

        elif not candidate_path.is_dir():

            st.error("路径不存在，或不是有效目录。")

        elif not (
            candidate_path / "workspace" / "task.json"
        ).is_file():

            st.warning(
                "目录存在，但未找到 task.json，"
                "无法识别为已有生产任务。"
            )

        elif not (
            candidate_path / "workspace" / "candidates_recomm"
        ).is_dir():

            st.warning(
                "任务目录存在，但尚未找到候选帧推荐记录。"
            )

        else:

            checked_path = candidate_path.resolve()
            path_ready = True

            st.success(
                "已找到生产任务目录及候选帧记录，可以读取。"
            )

    # ========================================================
    # 用户确认读取已有任务
    # ========================================================

    if st.button(
        "读取已有任务",
        type="primary",
        key="recovery_load_task",
        disabled=not path_ready,
    ):

        try:

            recovery = load_recovery_task(checked_path)

        except (
            ValueError,
            FileNotFoundError,
            OSError,
            KeyError,
        ) as exc:

            st.session_state.pop(
                "recovery_loaded_path",
                None,
            )

            st.error(f"读取任务失败：{exc}")

        else:

            st.session_state["recovery_loaded_path"] = str(
                recovery["task_dir"]
            )

            st.rerun()

    loaded_path = st.session_state.get(
        "recovery_loaded_path"
    )


    # ========================================================
    # 第二部分：读取已有任务并展示
    # ========================================================

    if (
            loaded_path is not None
            and path_ready
            and checked_path is not None
            and str(checked_path) == loaded_path
    ):

        try:

            recovery = load_recovery_task(loaded_path)

        except (
            ValueError,
            FileNotFoundError,
            OSError,
            KeyError,
        ) as exc:

            st.error(f"读取任务记录失败：{exc}")

        else:

            root = recovery["task_dir"]
            state = recovery["state"] or {}
            selection = recovery["selection"]
            approved_image = recovery["approved_image"]
            history = recovery["history"]

            stage = state.get("stage", "unknown")

            # 优先识别新版备份文件
            new_backup = root / "1f-original-bk.png"

            # 兼容旧版备份文件
            old_backup = root / "1f-original.png.bk"

            if new_backup.is_file():
                backup_file = new_backup
            elif old_backup.is_file():
                backup_file = old_backup
            else:
                backup_file = new_backup

            # ------------------------------------------------
            # 1. 判断是否已经执行最终替换
            # ------------------------------------------------

            selection_exists = selection is not None
            image_exists = approved_image is not None

            final_replaced = (
                stage == "candidate_replaced_final"
                or (
                    selection_exists
                    and (
                        selection.get("final_replacement") is True
                        or "selection_history" in selection
                    )
                )
                or backup_file.exists()
            )

            # ------------------------------------------------
            # 2. 检查记录是否一致
            # ------------------------------------------------

            records_inconsistent = (
                selection_exists != image_exists
            )

            # 状态宣称已审批，但没有审批记录
            if (
                not selection_exists
                and stage in (
                    "candidate_approved",
                    "candidate_replaced_final",
                )
            ):
                records_inconsistent = True

            valid_stages = {
                "inputs_saved",
                "awaiting_decision",
                "candidate_approved",
                "candidate_replaced_final",
            }

            state_invalid = stage not in valid_stages

            # ------------------------------------------------
            # 3. 是否允许选择历史候选帧
            # ------------------------------------------------

            can_choose = (
                not final_replaced
                and not records_inconsistent
                and not state_invalid
                and (
                    selection_exists
                    or stage in (
                        "inputs_saved",
                        "awaiting_decision",
                    )
                )
            )

            # ------------------------------------------------
            # 4. 显示上一次确认弹窗的操作结果
            # ------------------------------------------------

            saved = st.session_state.get(
                "recovery_saved_result"
            )

            if saved is not None:

                if saved.get("task_dir") == str(root):

                    if saved.get("mode") == "replace":
                        st.success(
                            "最终关键帧替换成功，"
                            "当前任务已锁定。"
                        )
                    else:
                        st.success(
                            "历史候选帧首次确认成功。"
                        )

                    st.caption("关键帧保存位置")
                    st.code(
                        saved["image_path"],
                        language=None,
                    )

                st.session_state.pop(
                    "recovery_saved_result",
                    None,
                )

            st.divider()

            # ------------------------------------------------
            # 5. 左右两栏
            # ------------------------------------------------

            left, right = st.columns(
                [1, 1.4],
                gap="large",
            )

            # =================================================
            # 左侧：任务状态及关键帧
            # =================================================

            with left:

                st.subheader("当前任务")

                # ---------------------------------------------
                # 当前业务状态
                # ---------------------------------------------

                if final_replaced:

                    display_status = "已最终替换（任务锁定）"

                elif records_inconsistent or state_invalid:

                    display_status = "任务记录异常（只读）"

                elif selection_exists:

                    display_status = "已首次确认（允许最终替换一次）"

                else:

                    display_status = "尚未确认候选帧"

                st.write(
                    f"**当前状态：** {display_status}"
                )

                st.write(
                    f"**历史推荐轮次：** {len(history)}"
                )

                st.caption("生产任务绝对路径")

                st.code(
                    str(root),
                    language=None,
                )

                # ---------------------------------------------
                # 已最终替换：醒目锁定提示
                # ---------------------------------------------

                if final_replaced:

                    st.error(
                        "**此任务已经完成二次确认与最终关键帧替换！**\n\n"
                        "该任务已被锁定，"
                        "无法再选择或替换任何候选帧。\n\n"
                        "**如果仍需调整，请新建生产任务。**"
                    )

                # ---------------------------------------------
                # 记录异常提示
                # ---------------------------------------------

                if records_inconsistent:

                    st.error(
                        "检测到审批记录与关键帧文件不一致。\n\n"
                        "为了避免破坏已有成果，"
                        "当前任务禁止任何新的关键帧选择操作。"
                    )

                elif state_invalid:

                    st.warning(
                        "任务状态记录缺失或不可识别。\n\n"
                        "当前可以查看已有图片，"
                        "但不允许继续修改。"
                    )

                # =============================================
                # 状态一：已经有当前关键帧
                # =============================================

                if image_exists:

                    st.divider()

                    if final_replaced:

                        st.subheader("最终确认的关键帧")

                    else:

                        st.subheader("当前已确认的关键帧")

                    # -----------------------------------------
                    # 大图：当前生效的 1f-original.png
                    # -----------------------------------------

                    st.image(
                        str(approved_image),
                        use_container_width=True,
                    )

                    if selection_exists:

                        st.write(
                            "**当前使用：** "
                            f"第 {selection.get('selected_round')} 轮 · "
                            f"候选 {selection.get('selected_rank')}"
                        )

                    st.caption(
                        "当前关键帧：1f-original.png"
                    )

                    st.code(
                        str(approved_image),
                        language=None,
                    )

                    # =========================================
                    # 状态三：已经最终替换
                    # =========================================

                    if final_replaced:

                        st.divider()

                        st.subheader(
                            "替换前的关键帧备份"
                        )

                        if backup_file.is_file():

                            # 小图：首次审批的原图片
                            st.image(
                                backup_file.read_bytes(),
                                width=220,
                            )

                            st.caption(
                                "这是首次确认的关键帧，"
                                "已经被最终版本替换。"
                            )

                            # 从审批历史中读取首次选择
                            if selection_exists:

                                choice_history = selection.get(
                                    "selection_history"
                                )

                                if (
                                    isinstance(choice_history, list)
                                    and choice_history
                                    and isinstance(
                                        choice_history[0], dict
                                    )
                                ):

                                    first_choice = choice_history[0]

                                    st.write(
                                        "**首次确认：** "
                                        f"第 {first_choice.get('selected_round')} 轮 · "
                                        f"候选 {first_choice.get('selected_rank')}"
                                    )

                            st.caption(
                                f"备份文件：{backup_file.name}"
                            )

                            st.code(
                                str(backup_file),
                                language=None,
                            )

                        else:
                            st.error(
                                "检测到最终替换记录，"
                                "但未找到关键帧备份文件。\n\n"
                                "请检查 1f-original-bk.png "
                                "或旧版 1f-original.png.bk 是否存在。"
                            )

                    # =========================================
                    # 状态二：首次审批，允许最终替换一次
                    # =========================================

                    elif can_choose:

                        st.info(
                            "当前任务已经完成首次确认，"
                            "尚未使用最终替换机会。\n\n"
                            "如果认可这张关键帧，"
                            "无需再次操作。\n\n"
                            "如果希望调整，"
                            "可以从右侧历史候选中"
                            "**最终替换一次**。"
                        )

                # =============================================
                # 状态一：尚未确认任何关键帧
                # =============================================

                else:

                    st.divider()

                    if final_replaced:

                        st.error(
                            "此任务曾经执行过最终替换，"
                            "但当前 1f-original.png 不存在。"
                        )

                        # 主图片丢失时，仍尽可能展示旧图备份
                        if backup_file.is_file():

                            st.subheader(
                                "可找回的原关键帧备份"
                            )

                            st.image(
                                backup_file.read_bytes(),
                                width=220,
                            )

                            st.code(
                                str(backup_file),
                                language=None,
                            )

                    elif records_inconsistent:

                        st.warning(
                            "当前缺少已审批的关键帧图片，"
                            "需要先检查已有任务文件。"
                        )

                    elif can_choose:

                        st.subheader("当前关键帧")

                        st.info(
                            "当前任务尚未确认任何关键帧。\n\n"
                            "请在右侧历史推荐中选择一张。"
                        )

                    else:

                        st.info(
                            "当前没有可显示的关键帧。"
                        )

            # =================================================
            # 右侧：历史 Qwen 候选
            # =================================================

            with right:

                st.subheader("历史推荐")

                if not history:

                    st.info(
                        "当前任务没有可供查看的完整历史候选帧。"
                    )

                else:

                    # 最新轮次优先展示
                    latest_round = history[-1]["round_number"]

                    for record in reversed(history):

                        number = record["round_number"]
                        candidates = record["candidates"]
                        round_dir = record["round_dir"]

                        with st.expander(
                            f"第 {number} 轮 · "
                            f"{len(candidates)} 张候选帧",
                            expanded=(
                                number == latest_round
                            ),
                        ):

                            if record.get("notes"):

                                st.caption(
                                    str(record["notes"])
                                )

                            if not candidates:

                                st.info(
                                    "本轮没有完整的候选帧。"
                                )

                                continue

                            # 每行三张缩略图
                            candidate_columns = st.columns(
                                3,
                                gap="small",
                            )

                            for index, candidate in enumerate(candidates):

                                rank = candidate["rank"]

                                image_path = (
                                    round_dir
                                    / candidate["image_file"]
                                )

                                # 是否为当前正在使用的关键帧
                                is_selected = (
                                    selection_exists
                                    and selection.get(
                                        "selected_round"
                                    ) == number
                                    and selection.get(
                                        "selected_rank"
                                    ) == rank
                                )

                                # 是否为最终替换前的首次选择
                                was_original = False

                                if final_replaced and selection_exists:

                                    choice_history = selection.get(
                                        "selection_history"
                                    )

                                    if (
                                        isinstance(
                                            choice_history, list
                                        )
                                        and choice_history
                                        and isinstance(
                                            choice_history[0], dict
                                        )
                                    ):

                                        first_choice = choice_history[0]

                                        was_original = (
                                            first_choice.get(
                                                "selected_round"
                                            ) == number
                                            and first_choice.get(
                                                "selected_rank"
                                            ) == rank
                                        )

                                # ---------------------------------
                                # 展示单张历史候选帧
                                # ---------------------------------

                                with candidate_columns[index % 3]:

                                    st.markdown(
                                        f"**候选 {rank}**"
                                    )

                                    if is_selected:

                                        st.success(
                                            "当前使用"
                                        )

                                    elif was_original:

                                        st.caption(
                                            "首次确认（已替换）"
                                        )

                                    st.image(
                                        str(image_path),
                                        use_container_width=True,
                                    )

                                    seconds = candidate.get(
                                        "time_seconds"
                                    )

                                    if isinstance(
                                        seconds,
                                        (int, float),
                                    ):

                                        st.caption(
                                            f"{seconds:.3f} 秒"
                                        )

                                    # -----------------------------
                                    # 推荐详情弹窗
                                    # -----------------------------

                                    with st.popover(
                                        "推荐详情",
                                        use_container_width=True,
                                    ):

                                        st.write(
                                            "镜头描述："
                                            + str(
                                                candidate.get(
                                                    "shot",
                                                    "未提供",
                                                )
                                            )
                                        )

                                        st.write(
                                            "推荐理由："
                                            + str(
                                                candidate.get(
                                                    "reason",
                                                    "未提供",
                                                )
                                            )
                                        )

                                        issues = candidate.get(
                                            "potential_issues",
                                            [],
                                        )

                                        if isinstance(
                                            issues, list
                                        ):

                                            st.write(
                                                "潜在问题："
                                                + (
                                                    "；".join(
                                                        map(str, issues)
                                                    )
                                                    or "未列出"
                                                )
                                            )

                                        else:

                                            st.write(
                                                f"潜在问题：{issues}"
                                            )

                                    # =============================
                                    # 按钮状态控制
                                    # =============================

                                    # 已最终替换：
                                    # 所有按钮灰色、不可点击
                                    if final_replaced:

                                        st.button(
                                            "已锁定 · 不可替换",
                                            disabled=True,
                                            key=(
                                                f"recovery_locked_"
                                                f"{number}_{rank}"
                                            ),
                                            use_container_width=True,
                                        )

                                    # 当前正在使用的候选
                                    elif is_selected:

                                        st.button(
                                            "当前使用",
                                            disabled=True,
                                            key=(
                                                f"recovery_current_"
                                                f"{number}_{rank}"
                                            ),
                                            use_container_width=True,
                                        )

                                    # 允许首次审批或最终替换
                                    elif can_choose:

                                        mode = (
                                            "replace"
                                            if selection_exists
                                            else "first"
                                        )

                                        button_label = (
                                            "最终替换"
                                            if mode == "replace"
                                            else "选择这张"
                                        )

                                        if st.button(
                                            button_label,
                                            key=(
                                                f"recovery_choose_"
                                                f"{number}_{rank}"
                                            ),
                                            use_container_width=True,
                                        ):

                                            _confirm_recovery_candidate(
                                                task_dir=root,
                                                round_number=number,
                                                rank=rank,
                                                image_path=image_path,
                                                mode=mode,
                                            )

                                    # 记录异常时，同样不允许操作
                                    else:

                                        st.button(
                                            "当前不可选择",
                                            disabled=True,
                                            key=(
                                                f"recovery_disabled_"
                                                f"{number}_{rank}"
                                            ),
                                            use_container_width=True,
                                        )

            st.divider()

    # ========================================================
    # 第三部分：放弃历史结果，新建生产任务
    # ========================================================

    if st.button(
        "不使用历史结果，新建生产任务",
        key="recovery_create_new_task",
    ):

        return "new_task"

    return None
