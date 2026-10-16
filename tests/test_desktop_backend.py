"""桌面壳后端测试（model-free）：事件行解析 / 会话→项目登记 / 会议库分组 / 配置类型守卫。

desktop/app.py 已把 webview 延迟导入——CI 无 pywebview 环境可 import 本模块并测后端逻辑。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "desktop"))

import app as desktop_app  # noqa: E402


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_uib_"))
        # 配置/注册表全部指到临时目录（绝不碰用户真实配置）
        desktop_app.CONFIG_DIR = self.tmp
        desktop_app.CONFIG_FILE = self.tmp / "ui_config.json"
        desktop_app.SESSMAP_FILE = self.tmp / "sessions_map.json"
        desktop_app.LLM_LOG_FILE = self.tmp / "llm.log"
        self.b = desktop_app.Backend()

    def _events(self, kind: str | None = None):
        return [e for e in self.b.poll_events(0)["events"] if kind is None or e["type"] == kind]

    def test_handle_line_types(self) -> None:
        self.b._handle_line("  >>> VAR! [L1] 热词:KG → clip abc（回溯300s，3句）")
        self.b._handle_line("  [14:32:07] 坤鑫: 就是嗯想问一下")  # 引擎 echo 为 HH:MM:SS
        self.b._handle_line("2026-10-15 [INFO] decoding, utt: noise")  # 噪声必须被过滤
        kinds = [e["type"] for e in self._events()]
        self.assertIn("hit", kinds)
        lines = self._events("line")
        self.assertEqual((lines[0]["clock"], lines[0]["spk"]), ("14:32:07", "坤鑫"))
        self.assertEqual(len(self._events("log")), 2, "funasr 噪声不应进入事件流")

    def test_ask_parsing(self) -> None:
        self.b._handle_line("[spk] 检测到新说话人 spk0（18:55:09 起）——刚才说：「你」")
        ask = self._events("ask")
        self.assertEqual(ask[0]["spk"], "spk0")
        self.assertEqual(ask[0]["sample"], "你")

    def test_done_registers_session_project(self) -> None:
        self.b._current_project = "D:/演示/项目A"
        self.b._handle_line("[done] session=go_1792049782644001900 / 0 句 / 1 片段 / 纠错 0 处")
        self.assertEqual(self.b._session_projects.get("go_1792049782644001900"), "D:/演示/项目A")
        self.assertTrue((self.tmp / "sessions_map.json").exists(), "登记应持久化（会议库分组依赖它）")

    def test_exit_event_carries_last_log(self) -> None:
        self.b._handle_line("Critical: 某处炸了")
        # 模拟 reader 的退出推送路径：直接检查 last_log 捕获
        self.assertEqual(self.b._last_log, "Critical: 某处炸了")

    def test_list_library_groups_by_project(self) -> None:
        pool = self.tmp / "repo" / "data" / "pool"
        for sid in ("go_1", "go_2", "replay_x"):
            (pool / sid).mkdir(parents=True)
            (pool / sid / "summary.json").write_text(
                json.dumps({"session_id": sid, "lines": 3, "clips": 1}), encoding="utf-8"
            )
        self.b._cfg["repo_dir"] = str(self.tmp / "repo")
        self.b._session_projects = {"go_1": "D:/演示/项目A", "go_2": "D:/演示/项目A"}
        groups = self.b.list_library()
        by_name = {g["name"]: g for g in groups}
        self.assertIn("项目A", by_name)
        self.assertEqual(len(by_name["项目A"]["sessions"]), 2)
        self.assertIn("（未归属会话）", by_name, "无映射的会话应进未归属组")

    def test_library_backfills_from_archive_dirs(self) -> None:
        """历史映射回填：<项目>/meetingvar/minutes/<sid>/ 存在即算该项目。"""
        proj = self.tmp / "projB"
        (proj / "meetingvar" / "minutes" / "go_9").mkdir(parents=True)
        pool = self.tmp / "repo" / "data" / "pool"
        (pool / "go_9").mkdir(parents=True)
        (pool / "go_9" / "summary.json").write_text(json.dumps({"lines": 1}), encoding="utf-8")
        self.b._cfg["repo_dir"] = str(self.tmp / "repo")
        self.b._cfg["projects"] = [str(proj)]
        groups = self.b.list_library()
        names = {g["name"] for g in groups}
        self.assertIn("projB", names)

    def test_llm_status_shape_and_safe_when_down(self) -> None:
        st = self.b.llm_status()
        for key in ("up", "model", "managed", "gguf"):
            self.assertIn(key, st, f"llm_status 必须带 {key} 字段（前端依赖）")
        self.assertIsInstance(st["up"], bool)

    def test_spawns_are_windowless(self) -> None:
        """GUI 壳启动的控制台子进程必须带 CREATE_NO_WINDOW。

        真机实测（2026-10-16）：缺该旗标时点开始会议/读取/起 LLM 都会弹出黑窗
        （用户描述"弹出一个脚本页面"），且会议期间一直挂着。
        """
        import subprocess as sp

        if os.name != "nt":
            self.skipTest("CREATE_NO_WINDOW 为 Windows 专属旗标")
        captured: dict = {}

        def fake_popen(cmd, **kw):
            captured["flags"] = kw.get("creationflags", 0)
            raise OSError("stop-after-capture")

        self.b._cfg["engine_python"] = sys.executable  # 存在即可过前置校验
        self.b._cfg["repo_dir"] = str(self.tmp)
        # 探测打成确定值，避免依赖本机是否恰好开着 LLM 服务
        no_llm = {"up": False, "model": "", "managed": False, "gguf": ""}
        with mock.patch.object(desktop_app.Backend, "llm_status", return_value=no_llm):
            with mock.patch.object(desktop_app.subprocess, "Popen", fake_popen):
                r = self.b.start_meeting(str(self.tmp), mode="record")
            self.assertFalse(r["ok"])
            self.assertTrue(captured.get("flags", 0) & sp.CREATE_NO_WINDOW, "引擎子进程必须无窗口")

            captured.clear()
            gguf = self.tmp / "m.gguf"
            gguf.write_bytes(b"x")
            self.b._cfg["llm_gguf"] = str(gguf)
            with mock.patch.object(desktop_app.subprocess, "Popen", fake_popen):
                self.b.llm_start()
            self.assertTrue(captured.get("flags", 0) & sp.CREATE_NO_WINDOW, "LLM 服务子进程必须无窗口")

    def test_set_config_type_guard(self) -> None:
        self.b.set_config({"use_llm": True, "projects": ["a", "b"], "device": "cpu", "bogus": "x"})
        self.assertTrue(self.b._cfg["use_llm"])
        self.assertEqual(self.b._cfg["projects"], ["a", "b"])
        self.assertEqual(self.b._cfg["device"], "cpu")
        self.assertNotIn("bogus", self.b._cfg)


if __name__ == "__main__":
    unittest.main()
