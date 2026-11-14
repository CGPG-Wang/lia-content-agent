
"""Lia Content Agent 应用入口：管理页面导航。"""
import streamlit as st
from lia_agent.ui.home import render_home
from lia_agent.ui.model_hub import render_model_hub
from lia_agent.scenes import SCENES
from lia_agent.ui.keyframe_recommendation import render_keyframe_recommendation
from lia_agent.ui.keyframe_review import render_keyframe_review
from lia_agent.workflows.keyframe_workflow import start_recommendation,continue_recommendation
from lia_agent.ui.keyframe_feedback import render_keyframe_feedback
from pathlib import Path
from lia_agent.ui.keyframe_recovery import render_keyframe_recovery


# 1. 应用基础配置
st.set_page_config(
    page_title="Lia Content Agent",
    layout="wide",
)


# 2. 初始化页面导航状态
if "current_page" not in st.session_state:
    st.session_state.current_page = "home"

if "selected_model" not in st.session_state:
    st.session_state.selected_model = None

if "selected_scene" not in st.session_state:
    st.session_state.selected_scene = None

if "keyframe_task_dir" not in st.session_state:
    st.session_state.keyframe_task_dir = None

if "keyframe_result" not in st.session_state:
    st.session_state.keyframe_result = None

if "keyframe_session" not in st.session_state:
    st.session_state.keyframe_session = None

# 3. 首页：选择视频模型
if st.session_state.current_page == "home":

    selected_model = render_home()

    if selected_model is not None:
        st.session_state.selected_model = selected_model
        st.session_state.current_page = "model_hub"
        st.rerun()


# 4. 模型场景页面（暂时使用占位页面）
elif st.session_state.current_page == "model_hub":

    if st.button("← 返回首页"):
        st.session_state.current_page = "home"
        st.session_state.selected_model = None
        st.session_state.selected_scene = None
        st.rerun()

    # 调用 model_hub.py，显示当前模型的所有场景
    selected_scene = render_model_hub(
        st.session_state.selected_model
    )


    # 根据注册表中的入口类型决定跳转页面
    if selected_scene is not None:

        scene = SCENES[selected_scene]

        entry_type = scene.get("entry_type", "creation")

        if entry_type == "recovery":

            # 恢复入口不需要进入新任务工作台
            st.session_state.selected_scene = None

            # 清理上次任务恢复页面的临时状态
            st.session_state.pop("recovery_loaded_path", None)
            st.session_state.pop("recovery_saved_result", None)
            st.session_state.pop("recovery_path_input", None)

            st.session_state.current_page = "keyframe_recovery"

        else:

            # 普通创作场景继续沿用原有逻辑
            st.session_state.selected_scene = selected_scene
            st.session_state.current_page = "workbench"

        st.rerun()



# 5. 三级页面：工作台
elif st.session_state.current_page == "workbench":

    if st.button("← 返回场景选择"):
        st.session_state.current_page = "model_hub"
        st.session_state.selected_scene = None
        st.rerun()

    scene_id = st.session_state.selected_scene

    # 如果已经选择具体场景，则显示候选帧推荐页面
    if scene_id is not None:

        saved_path = render_keyframe_recommendation(scene_id)

        if saved_path is not None:
            # 保存当前生产任务目录
            st.session_state.keyframe_task_dir = str(saved_path)

            # 新任务清空上一任务的会话及推荐结果
            st.session_state.keyframe_session = None
            st.session_state.keyframe_result = None

            # 切换到 Review 页面
            st.session_state.current_page = "keyframe_review"

            # 重新执行 app.py，渲染新页面
            st.rerun()

# 6. 候选帧 Review 页面
elif st.session_state.current_page == "keyframe_review":

    if st.button("← 返回工作台"):
        st.session_state.current_page = "workbench"
        st.rerun()

    task_dir = st.session_state.keyframe_task_dir

    if task_dir is None:
        st.warning("当前没有生产任务，请先创建任务。")

    else:
        # 尚未执行第一轮推荐
        if st.session_state.keyframe_result is None:

            st.info("任务已创建，可以开始第一轮候选帧推荐。")

            if st.button("开始第一轮推荐", type="primary"):

                try:
                    with st.spinner("正在分析视频并提取候选帧……"):

                        session, result = start_recommendation(
                            task_dir
                        )

                except Exception as exc:
                    st.error(
                        f"推荐失败：{type(exc).__name__}: {exc}"
                    )
                    st.warning(
                        "本版不会自动重试，请先检查本地留痕。"
                    )

                else:
                    # 保存 Workflow 返回的多轮会话
                    st.session_state.keyframe_session = session

                    # 保存最新一轮结果
                    st.session_state.keyframe_result = result

                    st.rerun()

        # 已经收到 Workflow 的推荐结果
        else:
            result = st.session_state.keyframe_result

            st.success(
                f"**Qwen 第 {result.round_number} 轮推荐完成！**\n\n"
                f"当前轮次：{result.round_number}\n\n"
                f"当前状态：{result.status}\n\n"
                f"候选数量：{len(result.candidates)}\n\n"
                f"留痕目录：{result.round_dir}"
            )

            action = render_keyframe_review(task_dir, result)

            if action is True:
                # 用户确认了一张候选帧
                st.session_state.current_page = "keyframe_success"
                st.rerun()

            elif action == "feedback":
                # 用户对本轮不满意，进入历史选择页面
                st.session_state.current_page = "keyframe_feedback"
                st.rerun()

# 7. 候选帧审批成功页面
elif st.session_state.current_page == "keyframe_success":
    # 获取已确认关键帧的绝对路径
    image_path = (
            Path(st.session_state.keyframe_task_dir).expanduser().resolve()
            / "1f-original.png"
    )

    st.title("候选帧选择成功！")

    st.success("候选帧选择成功！")
    st.code(str(image_path))

    st.caption(
        f"当前生产任务目录："
        f"{st.session_state.keyframe_task_dir}"
    )

    st.divider()

    # 两个导航按钮
    left, right = st.columns(2, gap="large")

    with left:
        if st.button(
            "返回首页",
            use_container_width=True,
        ):
            st.session_state.current_page = "home"
            st.rerun()

    with right:
        if st.button(
            "返回工作台",
            use_container_width=True,
        ):
            st.session_state.current_page = "model_hub"
            st.rerun()

# 8. 候选帧 Feedback 页面
elif st.session_state.current_page == "keyframe_feedback":

    task_dir = st.session_state.keyframe_task_dir

    if task_dir is None:
        st.error("没有找到当前生产任务目录。")

    else:
        if st.button("← 返回本轮推荐"):
            st.session_state.current_page = "keyframe_review"
            st.rerun()

        # 渲染 Feedback 页面并接收用户操作
        action = render_keyframe_feedback(task_dir)

        if action is True:
            st.session_state.current_page = "keyframe_success"
            st.rerun()

        elif (
                isinstance(action, tuple)
                and len(action) == 2
                and action[0] == "continue"
        ):
            feedback = action[1]
            session = st.session_state.get("keyframe_session")

            if session is None:
                st.session_state.pop("keyframe_feedback_pending", None)
                st.error("当前会话已失效，无法继续多轮推荐。")

            else:
                try:
                    with st.spinner("正在请求 Qwen 并提取新一轮候选帧……"):
                        new_result = continue_recommendation(
                            session=session,
                            feedback=feedback,
                        )

                except Exception as exc:
                    st.session_state.pop("keyframe_feedback_pending", None)
                    st.error(f"新一轮推荐失败：{type(exc).__name__}: {exc}")
                    st.warning("本次请求可能已经产生部分留痕。当前版本不会自动重试，请检查任务目录。")

                else:
                    st.session_state.pop("keyframe_feedback_pending", None)

                    # 清空输入框内容，避免回到 feedback 页面时保留旧文本
                    # st.session_state.keyframe_feedback_input = ""

                    # 保存最新一轮结果
                    st.session_state.keyframe_result = new_result

                    # 返回 review 页面展示新一轮结果
                    st.session_state.current_page = "keyframe_review"
                    st.rerun()


# 9. 候选帧任务恢复页面
elif st.session_state.current_page == "keyframe_recovery":

    if st.button(
        "← 返回场景选择",
        key="recovery_back_to_model_hub",
    ):

        st.session_state.pop("recovery_loaded_path", None)
        st.session_state.pop("recovery_saved_result", None)

        st.session_state.current_page = "model_hub"
        st.rerun()

    # 渲染任务恢复页面
    action = render_keyframe_recovery()

    # 用户放弃历史结果，决定新建生产任务
    if action == "new_task":

        # 清除旧任务的临时会话数据
        st.session_state.keyframe_task_dir = None
        st.session_state.keyframe_session = None
        st.session_state.keyframe_result = None

        st.session_state.pop("keyframe_feedback_pending", None)
        st.session_state.pop("recovery_loaded_path", None)
        st.session_state.pop("recovery_saved_result", None)

        # 新建任务进入当前已开放的单关键帧创作场景
        st.session_state.selected_model = "Seedance 2.5"

        st.session_state.selected_scene = (
            "SEEDANCE_MULTISHOT_SINGLE_KF_RESHAPE"
        )

        st.session_state.current_page = "workbench"

        st.rerun()
