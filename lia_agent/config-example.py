"""Lia Content Agent - Qwen / DashScope configuration template.

Setup:
    1. Copy this file to `lia_agent/config.py`.
    2. Replace the Base URL and API Key placeholders with your own values.
    3. Keep `lia_agent/config.py` local; do not commit it to Git.

This v0.1 application supports Qwen via Alibaba Cloud Model Studio
(DashScope). The configured model must be available to your account.
"""

# --- Required: DashScope connection settings ---
# DASHSCOPE_BASE_URL: Your DashScope API base URL, ending in /api/v1.
# Use the URL for the region/workspace where your API Key and model are available.
DASHSCOPE_BASE_URL = "YOUR_DASHSCOPE_BASE_URL"

# DASHSCOPE_API_KEY: Your private API credential for that DashScope workspace.
# Never commit or share a real API Key. This placeholder is intentionally invalid.
DASHSCOPE_API_KEY = "YOUR_DASHSCOPE_API_KEY"

# --- Existing model / frame settings ---
# These defaults were used by the original implementation. Change them only
# if your selected Qwen model and processing settings require it.
QWEN_MODEL = "qwen3.5-122b-a10b"
VIDEO_FPS = 2

# --- Configurable: candidate-frame selection rules ---
# KEYFRAME_RULES: Natural-language instructions defining what makes a useful
# source-video frame for subsequent Lia-KF creation (face visibility, outfit,
# body proportions, scene context, image quality, and frame diversity).
# Customize these instructions to match your own selection criteria.
# The default prompt is written in Chinese; it tells Qwen to respond in Chinese.
KEYFRAME_RULES = """分析本次上传的视频，推荐3张适合作为人物与环境参考的原视频画面，返回其时间点。用中文回答。
筛选规则：
1. 优先正脸，五官和脸型清楚、完整可见，面部尽量无遮挡。
2. 尽可能同时展示发型、身材比例、服装、配饰，以及背景、光线、人物与环境的空间关系。
3. 画面清晰；避开转场、严重运动模糊、闭眼、异常表情和肢体形态不稳定的瞬间。
4. 三张画面应有差异，优先不同镜头或不同人物状态，避免连续且几乎相同的画面。
按推荐程度排序，说明每张画面所在镜头、推荐原因和潜在问题。
时间从视频开头0秒算起，允许小数秒，不要限定整数秒；只按实际观察推荐，不编造时间点或镜头。
合适画面不足3张时，如实说明。收到后续反馈时，根据同一视频和反馈重新推荐。
"""

# --- Configurable: structured model output requirements ---
# KEYFRAME_OUTPUT_RULES: Instructions for the response format that the
# application's candidate parser expects. You can refine the wording, but
# DO NOT change the required JSON keys or their expected data types:
# candidates (list), rank (integer), time_seconds (number), shot (string),
# reason (string), potential_issues (list of strings), and notes (string).
# The parser relies on this schema to extract timestamps and descriptions.
KEYFRAME_OUTPUT_RULES = """
返回候选信息的 JSON 对象（不要加 Markdown 代码围栏）：
{"candidates":[{"rank":1,"time_seconds":1.5,"shot":"原视频镜头编号及简述",
"reason":"推荐原因","potential_issues":["潜在问题"]}],"notes":"必要说明"}
1.5只是格式示例，time_seconds必须使用实际观察到的秒数，可带小数；不足3张时在notes中说明。
只返回时间点和说明，不编造图片链接、图片 Base64 或替代原图。
"""

# Combined system prompt consumed by the existing Qwen workflow.
# Keep this expression unchanged unless you also update the workflow/parser.
KEYFRAME_PROMPT = KEYFRAME_RULES + KEYFRAME_OUTPUT_RULES
