"""第一批测试：配置应保持一致，UI 不需要依赖实际模型接口。"""

import unittest

from lia_agent.scenes import MODULE_LABELS, SCENES, models, scene_ids_for


class SceneConfigurationTests(unittest.TestCase):
    def test_every_scene_uses_a_known_module(self):
        for scene in SCENES.values():
            for module in scene["modules"]:
                self.assertIn(module, MODULE_LABELS)

    def test_enabled_scene_exists(self):
        self.assertTrue(any(scene["enabled"] for scene in SCENES.values()))

    def test_filtering_keeps_ids(self):
        for model in models():
            ids = scene_ids_for(model)
            self.assertTrue(ids)
            self.assertTrue(all(SCENES[scene_id]["model"] == model for scene_id in ids))


if __name__ == "__main__":
    unittest.main()
