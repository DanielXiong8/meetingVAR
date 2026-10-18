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
        self.b._cfg.update(interest_mode="manual", asr_engine="local")

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

    def test_config_defaults_have_asr_and_provider_keys(self) -> None:
        for k in ("asr_engine", "xfyun_dir", "llm_provider", "llm_base_url", "llm_model", "llm_api_key"):
            self.assertIn(k, desktop_app.CONFIG_DEFAULTS, f"缺配置默认值 {k}")

    def test_start_meeting_xfyun_requires_dir(self) -> None:
        self.b._cfg["engine_python"] = sys.executable
        self.b._cfg["asr_engine"] = "xfyun"
        self.b._cfg["xfyun_dir"] = ""
        r = self.b.start_meeting("", mode="record")
        self.assertFalse(r["ok"])
        self.assertIn("讯飞文件夹", r["error"])

    def test_start_meeting_xfyun_passes_dir_to_engine(self) -> None:
        captured: dict = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = list(cmd)
            raise OSError("stop-after-capture")

        self.b._cfg["engine_python"] = sys.executable
        self.b._cfg["repo_dir"] = str(self.tmp)
        self.b._cfg["asr_engine"] = "xfyun"
        self.b._cfg["xfyun_dir"] = str(self.tmp)
        no_llm = {"up": False, "model": "", "managed": False, "gguf": ""}
        with mock.patch.object(desktop_app.Backend, "llm_status", return_value=no_llm):
            with mock.patch.object(desktop_app.subprocess, "Popen", fake_popen):
                self.b.start_meeting("", mode="record")
        cmd = captured["cmd"]
        self.assertIn("--asr", cmd)
        self.assertEqual(cmd[cmd.index("--asr") + 1], "xfyun")
        self.assertEqual(cmd[cmd.index("--xfyun-cred") + 1], str(self.tmp))
        # 未选讯飞引擎时不应带这些参数
        self.b._cfg["asr_engine"] = "local"
        with mock.patch.object(desktop_app.Backend, "llm_status", return_value=no_llm):
            with mock.patch.object(desktop_app.subprocess, "Popen", fake_popen):
                self.b.start_meeting("", mode="record")
        self.assertEqual(captured["cmd"][captured["cmd"].index("--asr") + 1], "local")
        self.assertNotIn("--xfyun-cred", captured["cmd"])

    def test_start_meeting_custom_provider_injects_env(self) -> None:
        captured: dict = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = list(cmd)
            captured["env"] = dict(kw.get("env") or {})
            raise OSError("stop-after-capture")

        self.b._cfg.update({
            "engine_python": sys.executable, "repo_dir": str(self.tmp),
            "interest_mode": "api", "llm_provider": "custom",
            "llm_base_url": "https://api.example.com/v1", "llm_model": "demo-model",
            "llm_api_key": "sk-test-not-real",
        })
        with mock.patch.object(desktop_app.subprocess, "Popen", fake_popen):
            self.b.start_meeting("", mode="record")
        self.assertEqual(captured["cmd"][captured["cmd"].index("--interest") + 1], "api")
        self.assertIn("--no-ask-speakers", captured["cmd"])
        self.assertNotIn("--llm", captured["cmd"])
        env = captured["env"]
        self.assertEqual(env.get("MEETINGVAR_LLM_BASE_URL"), "https://api.example.com/v1")
        self.assertEqual(env.get("MEETINGVAR_LLM_MODEL"), "demo-model")
        self.assertEqual(env.get("MEETINGVAR_LLM_API_KEY"), "sk-test-not-real")

    def test_transcribe_media_guards(self) -> None:
        self.b._cfg["xfyun_dir"] = ""
        r = self.b.transcribe_media_start(str(self.tmp / "x.mp4"))
        self.assertFalse(r["ok"])
        self.b._cfg["xfyun_dir"] = str(self.tmp)
        self.b._cfg["engine_python"] = sys.executable
        r2 = self.b.transcribe_media_start(str(self.tmp / "nope.mp4"))
        self.assertFalse(r2["ok"], "文件不存在必须被拦下")
        st = self.b.transcribe_media_status()
        self.assertIn("running", st)

    def test_llm_env_requires_ready_provider(self) -> None:
        """全库理解/开会共用 _llm_env：自定义厂商没填 Base URL 或本地服务没起时必须给人话错误。"""
        no_llm = {"up": False, "model": "", "managed": False, "gguf": ""}
        self.b._cfg["llm_provider"] = "custom"
        self.b._cfg["llm_base_url"] = ""
        with mock.patch.object(desktop_app.Backend, "llm_status", return_value=no_llm):
            with self.assertRaises(ValueError) as ctx:
                self.b._llm_env()
            self.assertIn("Base URL", str(ctx.exception))
            info = self.b._provider_info()
            self.assertFalse(info["ready"])
            self.assertIn("未配置", info["error"])
            # 本地服务也没起 → 同样明确报错
            self.b._cfg["llm_provider"] = "local"
            with self.assertRaises(ValueError) as ctx2:
                self.b._llm_env()
            self.assertIn("未启动", str(ctx2.exception))
        # 自定义厂商填了 base_url → 就绪，环境变量指向它
        self.b._cfg["llm_provider"] = "custom"
        self.b._cfg["llm_base_url"] = "https://api.example.com/v1"
        self.b._cfg["llm_model"] = "m1"
        env = self.b._llm_env()
        self.assertEqual(env["MEETINGVAR_LLM_BASE_URL"], "https://api.example.com/v1")
        self.assertEqual(env["MEETINGVAR_LLM_MODEL"], "m1")
        self.assertFalse(self.b._provider_info()["ready"], "填写配置不等于生成验证通过")
        self.b._llm_verified.add(self.b._llm_fingerprint(env))
        self.assertTrue(self.b._provider_info()["ready"])

    def test_understand_start_blocked_when_provider_not_ready(self) -> None:
        import subprocess as sp

        self.b._cfg["engine_python"] = sys.executable
        self.b._cfg["llm_provider"] = "custom"
        self.b._cfg["llm_base_url"] = ""
        called = {"popen": False}

        def fake_popen(*a, **k):
            called["popen"] = True
            raise sp.TimeoutExpired("x", 0)

        with mock.patch.object(desktop_app, "_SPAWN_FLAGS", desktop_app._SPAWN_FLAGS), \
             mock.patch.object(desktop_app.subprocess, "Popen", fake_popen):
            r = self.b.understand_start(str(self.tmp), 5)
        self.assertFalse(r["ok"])
        self.assertIn("Base URL", r["error"])
        self.assertFalse(called["popen"], "未就绪时不得启动子进程")

    def test_open_dirs_are_distinct(self) -> None:
        """「打开产物文件夹」与「打开配置目录」必须打开不同目录。

        曾同为 open_path("")→repo_dir（两个按钮打开同一个地方，属实现缺陷）。
        """
        opened: list[str] = []
        with mock.patch.object(desktop_app, "os") as fake_os:
            fake_os.startfile = lambda p: opened.append(str(p))
            self.b._cfg["repo_dir"] = str(self.tmp)
            self.b._cfg["last_project"] = ""
            self.b.open_config_dir()
            self.b.open_data_dir()
        self.assertEqual(len(opened), 2)
        self.assertEqual(Path(opened[0]), desktop_app.CONFIG_DIR)
        self.assertEqual(Path(opened[1]), self.tmp, "无项目/无 data 时回落仓库目录")

    def test_shutdown_stops_engine_and_llm(self) -> None:
        class _FakeProc:
            def __init__(self) -> None:
                self.stdin = mock.MagicMock()
                self._alive = True

            def poll(self):
                return None if self._alive else 0

            def wait(self, timeout=None):
                self._alive = False
                return 0

            def terminate(self):
                self._alive = False

        eng, llm = _FakeProc(), _FakeProc()
        self.b._proc = eng
        self.b._llm_proc = llm
        self.b.shutdown()
        self.assertIn(":stop", eng.stdin.write.call_args.args[0], "关窗必须先发优雅停止")
        self.assertFalse(llm._alive, "本应用启动的 LLM 服务应被终止（防孤儿进程占内存）")


if __name__ == "__main__":
    unittest.main()
