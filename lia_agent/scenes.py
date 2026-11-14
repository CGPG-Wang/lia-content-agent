"""场景注册表：仅描述有哪些场景和可用功能，不在这里执行功能。"""

MODULE_LABELS = {
    "candidate_frames": "候选帧推荐",
    "story_prompt": "剧情 / Prompt 设计",
}

SCENES = {
    "SEEDANCE_MULTISHOT_SINGLE_KF_RESHAPE": {
        "model": "Seedance 2.5",
        "label": "N 镜头 Instagram 日常短视频｜单关键帧重塑",
        "modules": ("candidate_frames",),
        "enabled": True,
    },
    "SEEDANCE_KEYFRAME_RECOVERY": {
        "model": "Seedance 2.5",
        "label": "恢复已有任务",
        "modules": (),
        "enabled": True,
        "entry_type": "recovery",
    },
    "SEEDANCE_SINGLE_REPLICA": {
        "model": "Seedance 2.5",
        "label": "单人复刻",
        "modules": ("story_prompt",),
        "enabled": False,
    },
    "SEEDANCE_DOUBLE_REPLICA": {
        "model": "Seedance 2.5",
        "label": "双人复刻",
        "modules": ("story_prompt",),
        "enabled": False,
    },
    "KLING_O3_EXACT": {
        "model": "Kling O3",
        "label": "Exact 复刻",
        "modules": ("candidate_frames",),
        "enabled": False,
    },
    "KLING_O3_PARTIAL": {
        "model": "Kling O3",
        "label": "Partial 复刻",
        "modules": ("candidate_frames",),
        "enabled": False,
    },
}


def models():
    """按配置中的顺序返回视频模型列表。"""
    return tuple(dict.fromkeys(scene["model"] for scene in SCENES.values()))


def scene_ids_for(model):
    """根据一级模型筛选子场景，返回稳定的场景 ID。"""
    return tuple(scene_id for scene_id, scene in SCENES.items() if scene["model"] == model)
