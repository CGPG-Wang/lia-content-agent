import streamlit as st


def render_home() -> str | None:
    """显示模型选择首页，返回用户选择的模型。"""

    st.title("Lia Content Agent")
    st.caption("请选择创作模型")

    left, right = st.columns(2, gap="large")

    with left:
        if st.button(
            "Seedance 2.5",
            use_container_width=True,
        ):
            return "Seedance 2.5"

    with right:
        if st.button(
            "Kling O3",
            use_container_width=True,
        ):
            return "Kling O3"

    return None