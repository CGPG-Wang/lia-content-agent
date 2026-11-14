
"""模型场景选择页面。"""

import streamlit as st

from lia_agent.scenes import SCENES, scene_ids_for


def render_model_hub(model: str) -> str | None:
    """显示指定模型下的场景，返回用户选择的场景 ID。"""

    st.title(model)
    st.caption("请选择创作场景或恢复已有任务")

    scene_ids = scene_ids_for(model)

    # 每行显示两个场景入口
    columns = st.columns(2, gap="large")

    for index, scene_id in enumerate(scene_ids):
        scene = SCENES[scene_id]

        label = scene["label"]

        if not scene["enabled"]:
            label += "（暂未开放）"

        with columns[index % 2]:
            if st.button(
                label,
                key=f"scene_{scene_id}",
                disabled=not scene["enabled"],
                use_container_width=True,
            ):
                return scene_id

    return None
