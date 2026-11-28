"""桌面壳后端测试（model-free）：事件行解析 / 会话→项目登记 / 会议库分组 / 配置类型守卫。

desktop/app.py 已把 webview 延迟导入——CI 无 pywebview 环境可 import 本模块并测后端逻辑。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.request
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
        desktop_app.SESSION_TITLES_FILE = self.tmp / "session_titles.json"
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

    def test_rename_session_persists_and_survives_reload(self) -> None:
        pool = self.tmp / "repo" / "data" / "pool"
        (pool / "go_1").mkdir(parents=True)
        (pool / "go_1" / "summary.json").write_text(
            json.dumps({"lines": 2, "clips": 1}), encoding="utf-8"
        )
        self.b._cfg["repo_dir"] = str(self.tmp / "repo")

        result = self.b.rename_session("go_1", "  产品周会 · 11/24  ")
        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "产品周会 · 11/24")
        self.assertEqual(self.b.list_sessions()[0]["title"], "产品周会 · 11/24")
        self.assertTrue((self.tmp / "session_titles.json").exists(), "标题应持久化")

        reloaded = desktop_app.Backend()
        reloaded._cfg["repo_dir"] = str(self.tmp / "repo")
        self.assertEqual(reloaded.list_sessions()[0]["title"], "产品周会 · 11/24",
                         "重启后重命名必须还在")

        self.b.rename_session("go_1", "   ")
        self.assertEqual(self.b.list_sessions()[0]["title"], "", "空标题应清除自定义名")
        self.assertFalse(self.b.rename_session("bad id", "x")["ok"], "非法会话 ID 必须拒绝")

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

    def test_agnes_uses_500k_context(self) -> None:
        self.b._cfg.update(llm_provider="custom", llm_base_url="https://api.example.com/v1",
                           llm_model="agnes-3.0-flash", llm_api_key="test-only",
                           llm_api_context_tokens=32768)
        env = self.b._llm_env("custom")
        self.assertEqual(env["MEETINGVAR_LLM_CONTEXT_TOKENS"], "500000")
        self.assertEqual(env["MEETINGVAR_LLM_MAX_TOKENS"], "8192")

    def test_saved_agnes_config_migrates_to_500k(self) -> None:
        desktop_app.CONFIG_FILE.write_text(json.dumps({
            "llm_provider": "custom", "llm_model": "agnes-3.0-flash",
            "llm_api_context_tokens": 32768,
        }), encoding="utf-8")
        loaded = desktop_app.load_config()
        self.assertEqual(loaded["llm_api_context_tokens"], 500000)

    def test_start_meeting_xfyun_requires_dir(self) -> None:
        self.b._cfg["engine_python"] = sys.executable
        self.b._cfg["asr_engine"] = "xfyun"
        self.b._cfg["xfyun_dir"] = ""
        r = self.b._start_meeting("", mode="record")
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
                self.b._start_meeting("", mode="record")
        cmd = captured["cmd"]
        self.assertIn("--asr", cmd)
        self.assertEqual(cmd[cmd.index("--asr") + 1], "xfyun")
        self.assertEqual(cmd[cmd.index("--xfyun-cred") + 1], str(self.tmp))
        # 未选讯飞引擎时不应带这些参数
        self.b._cfg["asr_engine"] = "local"
        with mock.patch.object(desktop_app.Backend, "llm_status", return_value=no_llm):
            with mock.patch.object(desktop_app.subprocess, "Popen", fake_popen):
                self.b._start_meeting("", mode="record")
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
            self.b._start_meeting("", mode="record")
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

    def test_usage_stats_aggregates_agent_trace(self) -> None:
        project = self.tmp / "project"
        trace = project / "meetingvar" / "agent_memory" / "trace"
        trace.mkdir(parents=True)
        rows = [
            {"event": "llm_call", "ts": 1792400000, "model": "local-a", "ok": True,
             "prompt_tokens": 100, "completion_tokens": 20, "reasoning_tokens": 5},
            {"event": "llm_call", "ts": 1792400100, "model": "local-a", "ok": False,
             "prompt_tokens": 50, "completion_tokens": 10, "reasoning_tokens": 0},
            {"event": "tool_started", "ts": 1792400200},
        ]
        (trace / "agent_run.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        self.b._cfg["projects"] = [str(project)]
        self.b._cfg["last_project"] = str(project)
        stats = self.b.usage_stats()
        self.assertTrue(stats["ok"])
        self.assertEqual(stats["totals"]["calls"], 2)
        self.assertEqual(stats["totals"]["tokens"], 180)
        self.assertEqual(stats["totals"]["errors"], 1)
        self.assertEqual(len(stats["days"]), 90)
        self.assertEqual(stats["models"][0]["model"], "local-a")
        self.assertEqual(stats["models"][0]["tokens"], 180)

    def test_project_file_url_serves_pdf_and_blocks_traversal(self) -> None:
        pdf = self.tmp / "sample.pdf"
        payload = b"%PDF-1.4\n%%EOF\n"
        pdf.write_bytes(payload)
        url = self.b.project_file_url(str(self.tmp), "sample.pdf")
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                self.assertEqual(resp.read(), payload)
                self.assertEqual(resp.headers.get("Content-Type"), "application/pdf")
            self.assertEqual(self.b.project_file_url(str(self.tmp), "../secret.txt"), "")
        finally:
            if self.b._project_server is not None:
                self.b._project_server.shutdown()
                self.b._project_server.server_close()

    def test_chat_send_assigns_distinct_uuid_runs(self) -> None:
        from uuid import UUID
        with mock.patch.object(self.b, "_agent_bridge_stream", return_value={"ok": True}) as bridge:
            self.b.chat_send(str(self.tmp), "thread-a", "first")
            first = bridge.call_args.kwargs["identity"]
            self.b.chat_send(str(self.tmp), "thread-a", "second")
            second = bridge.call_args.kwargs["identity"]
        self.assertNotEqual(first["run_id"], second["run_id"])
        UUID(first["run_id"])
        self.assertEqual(first["folder"], str(self.tmp))
        self.assertEqual(first["conversation_id"], "thread-a")

    def test_chat_busy_does_not_overwrite_process(self) -> None:
        self.b._cfg["engine_python"] = sys.executable
        original = mock.Mock()
        self.b._chat_proc = original
        self.b._chat_identity = {"run_id": "old"}
        identity = {"folder": str(self.tmp), "conversation_id": "thread-a", "run_id": "new"}
        with mock.patch.object(desktop_app.subprocess, "Popen") as spawn:
            result = self.b._agent_bridge_stream([], identity=identity)
        self.assertEqual(result["error_code"], "chat_busy")
        self.assertIs(self.b._chat_proc, original)
        spawn.assert_not_called()

    def test_chat_cancel_requires_matching_project_thread_and_run(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        self.b._chat_proc = process
        self.b._chat_identity = {"folder": str(self.tmp), "conversation_id": "thread-a", "run_id": "run-a"}
        self.assertFalse(self.b.chat_cancel()["ok"])
        self.assertFalse(self.b.chat_cancel(str(self.tmp), "thread-b", "run-a")["ok"])
        self.assertFalse(self.b.chat_cancel(str(self.tmp), "thread-a", "run-b")["ok"])
        process.kill.assert_not_called()
        self.assertTrue(self.b.chat_cancel(str(self.tmp), "thread-a", "run-a")["ok"])
        process.kill.assert_called_once()

    def test_stream_progress_cannot_override_invocation_identity(self) -> None:
        import io
        self.b._cfg["engine_python"] = sys.executable
        identity = {"folder": str(self.tmp), "conversation_id": "thread-a", "run_id": "run-a"}
        event = {"type": "agent_progress", "phase": "tool", "call_id": "call-a",
                 "folder": "wrong", "conversation_id": "wrong", "run_id": "wrong"}
        process = mock.Mock()
        process.stdout = io.StringIO("@@MEETINGVAR_EVENT " + json.dumps(event) + "\n{\"ok\": true}\n")
        process.wait.return_value = 0
        process.poll.return_value = 0
        with mock.patch.object(desktop_app.subprocess, "Popen", return_value=process):
            result = self.b._agent_bridge_stream([], identity=identity)
        self.assertEqual(result["run_id"], "run-a")
        progress = self._events("agent_progress")[0]
        for key, value in identity.items():
            self.assertEqual(progress[key], value)
        self.assertEqual(progress["call_id"], "call-a")
        self.assertIsNone(self.b._chat_identity)

    def test_ui_approval_dispatches_failure_receipt_not_confirmation(self) -> None:
        approved = {"ok": True, "ui_only": True, "action": "start_meeting", "status": "running"}
        with mock.patch.object(self.b, "_agent_bridge", return_value=approved), \
             mock.patch.object(self.b, "_start_meeting", return_value={"ok": False, "error": "spawn failed"}) as start, \
             mock.patch.object(self.b, "_finalize_chat_action", return_value={"ok": False, "status": "failed"}) as finalize:
            result = self.b.chat_action(str(self.tmp), "thread-a", "action-a")
        start.assert_called_once_with(str(self.tmp))
        self.assertFalse(finalize.call_args.args[3]["ok"])
        self.assertEqual(result["status"], "failed")

    def test_duplicate_approval_does_not_dispatch(self) -> None:
        with mock.patch.object(self.b, "_agent_bridge", return_value={"ok": False, "ui_only": False}), \
             mock.patch.object(self.b, "_start_meeting") as start:
            self.b.chat_action(str(self.tmp), "thread-a", "action-a")
        start.assert_not_called()

    def test_understand_approval_waits_for_reader_receipt(self) -> None:
        approved = {"ok": True, "ui_only": True, "action": "start_project_reading", "status": "running"}
        with mock.patch.object(self.b, "_agent_bridge", return_value=approved), \
             mock.patch.object(self.b, "_understand_start", return_value={"ok": True}) as start, \
             mock.patch.object(self.b, "_finalize_chat_action") as finalize:
            result = self.b.chat_action(str(self.tmp), "thread-a", "action-a")
        self.assertEqual(result["status"], "running")
        self.assertEqual(start.call_args.kwargs["action_context"], (str(self.tmp), "thread-a", "action-a"))
        finalize.assert_not_called()

    def test_empty_and_malformed_manifest_cannot_verify(self) -> None:
        target = self.tmp / "meetingvar" / "evidence_packs" / "pack"
        target.mkdir(parents=True)
        for manifest in ({}, {"files": []}, {"files": [None]}, [], {"files": [{"path": "x", "sha256": "bad"}]}):
            (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertFalse(self.b.evidence_verify(str(self.tmp), "pack")["ok"])

    def test_preview_token_keeps_original_project_root(self) -> None:
        from types import SimpleNamespace
        from urllib.parse import urlsplit
        first, second = self.tmp / "a", self.tmp / "b"
        first.mkdir()
        second.mkdir()
        (first / "same.pdf").write_bytes(b"first")
        (second / "same.pdf").write_bytes(b"second")
        with mock.patch.object(self.b, "_ensure_project_server"):
            url_a = self.b.project_file_url(str(first), "same.pdf")
            url_b = self.b.project_file_url(str(second), "same.pdf")
        handler = object.__new__(desktop_app._ProjectFileHandler)
        handler.server = SimpleNamespace(project_roots=self.b._project_roots)
        self.assertEqual(handler._target(urlsplit(url_a).path), (first / "same.pdf").resolve())
        self.assertEqual(handler._target(urlsplit(url_b).path), (second / "same.pdf").resolve())
        self.assertIsNone(handler._target("/unknown/same.pdf"))
        token = urlsplit(url_a).path.split("/")[1]
        self.assertIsNone(handler._target(f"/{token}/../b/same.pdf"))

    def test_kg_list_never_consolidates(self) -> None:
        import engine_bridge
        with mock.patch("meetingvar.kg.store.GraphStore") as store_type:
            store = store_type.return_value
            store.stats.return_value = {"nodes": 1, "edges": 0}
            store.nodes.return_value = []
            store.edges.return_value = []
            self.assertTrue(engine_bridge.cmd_kg_list(self.tmp)["ok"])
            store.consolidate_duplicates.assert_not_called()

    def test_bridge_journal_excludes_readonly_and_chat_commands(self) -> None:
        import engine_bridge
        for command in ("kg_list", "kg_search", "read_project_file", "agent_chat_send", "agent_chat_action", "chat_action_finalize"):
            self.assertNotIn(command, engine_bridge._MUTATION_COMMANDS)
        for command in ("kg_add_node", "kg_delete_edge", "agent_memory_resolve", "digest_session"):
            self.assertIn(command, engine_bridge._MUTATION_COMMANDS)

    def test_projection_failure_is_explicit_after_fact_mutation(self) -> None:
        import engine_bridge
        store = mock.Mock()
        store.finish.return_value = {"status": "failed"}
        with mock.patch("meetingvar.kg.build.GraphBuilder") as builder:
            builder.return_value.build.side_effect = ValueError("projection failed")
            result = engine_bridge._finish_operation((store, "op-a", self.tmp), "agent_memory_delete", {"ok": True})
        self.assertFalse(result["ok"])
        self.assertTrue(result["projection_pending"])
        self.assertTrue(result["mutation_applied"])
        builder.return_value.build.assert_called_once_with(include_meetings=True, reset=False)

    def _local_journal_bridge(self, args, **kwargs):
        import engine_bridge
        if args[0] == "host_operation_begin":
            return engine_bridge.cmd_host_operation_begin(Path(args[1]), args[2], args[3], Path(args[4]))
        if args[0] == "host_operation_finish":
            return engine_bridge.cmd_host_operation_finish(Path(args[1]), args[2], Path(args[3]))
        raise AssertionError(f"Unexpected command: {args[0]}")

    def test_owner_environment_matches_real_desktop_process(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows process identity")
        from meetingvar.agent.recovery import owner_identity
        with mock.patch.dict(os.environ, {"MEETINGVAR_OPERATION_OWNER_PID": "1",
                                         "MEETINGVAR_OPERATION_OWNER_START": "forged"}):
            env = self.b._engine_env()
        owner = owner_identity()
        self.assertEqual(env["MEETINGVAR_OPERATION_OWNER_PID"], str(os.getpid()))
        self.assertEqual(env["MEETINGVAR_OPERATION_OWNER_START"], owner["owner_start"])

    def test_manual_var_journals_only_actual_control_submission(self) -> None:
        from meetingvar.agent.operations import OperationStore
        process = mock.Mock()
        process.poll.return_value = None
        self.b._proc = process
        self.b._current_project = str(self.tmp)
        with mock.patch.object(self.b, "_agent_bridge", side_effect=self._local_journal_bridge), \
             mock.patch.object(self.b, "_send_control", return_value=True) as send:
            result = self.b.manual_var()
        self.assertTrue(result["ok"])
        send.assert_called_once_with(":var")
        row = OperationStore(self.tmp).get(result["operation_id"])
        self.assertEqual(row["kind"], "host.manual_var")
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["result"]["confirmation"], "command_sent_not_clip_verified")
        self.assertEqual(row["artifacts"], [])

    def test_manual_var_without_running_meeting_has_no_journal_or_control(self) -> None:
        with mock.patch.object(self.b, "_host_operation_begin") as begin, \
             mock.patch.object(self.b, "_send_control") as control:
            self.assertFalse(self.b.manual_var()["ok"])
        begin.assert_not_called()
        control.assert_not_called()

    def test_control_does_not_execute_when_journal_begin_fails(self) -> None:
        self.b._proc = mock.Mock()
        self.b._proc.poll.return_value = None
        self.b._current_project = str(self.tmp)
        with mock.patch.object(self.b, "_host_operation_begin", return_value={"ok": False}), \
             mock.patch.object(self.b, "_send_control") as control:
            self.assertFalse(self.b.manual_var()["ok"])
        control.assert_not_called()

    def test_speaker_name_submission_journals_and_rejects_control_injection(self) -> None:
        from meetingvar.agent.operations import OperationStore
        self.b._proc = mock.Mock()
        self.b._proc.poll.return_value = None
        self.b._current_project = str(self.tmp)
        with mock.patch.object(self.b, "_agent_bridge", side_effect=self._local_journal_bridge), \
             mock.patch.object(self.b, "_write_stdin", return_value={"ok": True}) as send:
            for text in ("Alice\n:stop", ":var", "Alice\x00"):
                self.assertFalse(self.b.answer_prompt(text)["ok"])
            send.assert_not_called()
            result = self.b.answer_prompt(" Alice Smith ")
        send.assert_called_once_with("Alice Smith\n")
        row = OperationStore(self.tmp).get(result["operation_id"])
        self.assertEqual(row["kind"], "host.answer_prompt")
        self.assertEqual(row["arguments"]["name"], "Alice Smith")
        self.assertEqual(row["result"]["confirmation"], "name_sent_not_identity_verified")

    def test_control_write_failure_is_not_success(self) -> None:
        from meetingvar.agent.operations import OperationStore
        self.b._proc = mock.Mock()
        self.b._proc.poll.return_value = None
        self.b._current_project = str(self.tmp)
        with mock.patch.object(self.b, "_agent_bridge", side_effect=self._local_journal_bridge), \
             mock.patch.object(self.b, "_send_control", return_value=False), \
             mock.patch.object(self.b, "_write_stdin", return_value={"ok": False, "error": "closed"}):
            result = self.b.manual_var()
        self.assertFalse(result["ok"])
        self.assertEqual(OperationStore(self.tmp).get(result["operation_id"])["status"], "failed")

    def test_export_actual_archive_receipt_and_external_scope(self) -> None:
        import hashlib
        import zipfile
        from meetingvar.agent.operations import OperationStore
        project = self.tmp / "project"
        source = project / "meetingvar"
        source.mkdir(parents=True)
        (source / "terms.txt").write_text("synthetic", encoding="utf-8")
        destination = self.tmp / "exports"
        destination.mkdir()
        with mock.patch.object(self.b, "_agent_bridge", side_effect=self._local_journal_bridge):
            result = self.b.export_project_data(str(project), str(destination))
        self.assertTrue(result["ok"])
        archive = Path(result["path"])
        with zipfile.ZipFile(archive) as exported:
            self.assertEqual(exported.read("meetingvar/terms.txt"), b"synthetic")
        row = OperationStore(project).get(result["operation_id"])
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["result"]["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(row["result"]["artifact_scope"], "outside_project_or_unverified")
        self.assertEqual(row["artifacts"], [])

    def test_export_into_its_source_is_rejected_without_journal(self) -> None:
        source = self.tmp / "meetingvar"
        source.mkdir()
        with mock.patch.object(self.b, "_host_operation_begin") as begin:
            self.assertFalse(self.b.export_project_data(str(self.tmp), str(source))["ok"])
        begin.assert_not_called()

    def test_delete_requires_existing_confirmation_before_any_journal(self) -> None:
        with mock.patch.object(self.b, "_host_operation_begin") as begin, \
             mock.patch("meetingvar.commercial.data.delete_categories") as delete:
            self.assertFalse(self.b.delete_project_data(str(self.tmp), ["terms"], "yes")["ok"])
            self.assertFalse(self.b.delete_project_data(str(self.tmp), [{}], "DELETE")["ok"])
        begin.assert_not_called()
        delete.assert_not_called()

    def test_delete_non_agent_category_keeps_exact_scope_and_receipt(self) -> None:
        from meetingvar.agent.operations import OperationStore
        project = self.tmp / "project"
        source = project / "meetingvar"
        source.mkdir(parents=True)
        (source / "terms.txt").write_text("synthetic", encoding="utf-8")
        minutes = source / "minutes"
        minutes.mkdir()
        with mock.patch.object(self.b, "_agent_bridge", side_effect=self._local_journal_bridge):
            result = self.b.delete_project_data(str(project), ["terms"], "DELETE")
        self.assertTrue(result["ok"])
        self.assertFalse((source / "terms.txt").exists())
        self.assertTrue(minutes.is_dir())
        row = OperationStore(project).get(result["operation_id"])
        self.assertEqual(row["arguments"]["categories"], ["terms"])
        self.assertEqual(row["status"], "succeeded")

    def test_delete_agent_erases_own_journal_without_resurrection_or_config_copy(self) -> None:
        project = self.tmp / "project"
        memory = project / "meetingvar" / "agent_memory"
        memory.mkdir(parents=True)
        (memory / "synthetic.txt").write_text("private synthetic content", encoding="utf-8")
        before = {path.name for path in self.tmp.iterdir()}
        with mock.patch.object(self.b, "_agent_bridge", side_effect=self._local_journal_bridge), \
             mock.patch.object(self.b, "_host_operation_finish", wraps=self.b._host_operation_finish) as finish:
            result = self.b.delete_project_data(str(project), ["agent"], "DELETE")
        self.assertTrue(result["ok"])
        self.assertTrue(result["erased_own_journal"])
        self.assertFalse(result["durable_receipt"])
        self.assertFalse(memory.exists())
        finish.assert_not_called()
        self.assertEqual({path.name for path in self.tmp.iterdir()}, before)

    def test_delete_partial_failure_is_not_success(self) -> None:
        from meetingvar.agent.operations import OperationStore
        source = self.tmp / "meetingvar"
        source.mkdir()
        (source / "terms.txt").write_text("synthetic", encoding="utf-8")
        with mock.patch.object(self.b, "_agent_bridge", side_effect=self._local_journal_bridge), \
             mock.patch("meetingvar.commercial.data.delete_categories", return_value={"ok": True, "removed": []}):
            result = self.b.delete_project_data(str(self.tmp), ["terms"], "DELETE")
        self.assertFalse(result["ok"])
        self.assertEqual(OperationStore(self.tmp).get(result["operation_id"])["status"], "failed")

    def test_reconcile_is_current_project_only_and_preserves_live_owner(self) -> None:
        import engine_bridge
        from meetingvar.agent.operations import OperationStore
        first, second = self.tmp / "first", self.tmp / "second"
        first.mkdir(); second.mkdir()
        stores = [OperationStore(first), OperationStore(second)]
        for store in stores:
            store.create("synthetic", operation_id="running")
            store.claim("running")
        live = engine_bridge._reconcile_project_operations(first)
        self.assertEqual(live["interrupted"], 0)
        self.assertIn("storage", live)
        with mock.patch("meetingvar.agent.operations.orphan_reason", return_value="owner_dead"):
            recovered = engine_bridge._reconcile_project_operations(first)
        self.assertEqual(recovered["interrupted"], 1)
        self.assertEqual(stores[0].get("running")["status"], "interrupted")
        self.assertEqual(stores[1].get("running")["status"], "running")

    def test_empty_project_recovery_does_not_create_journal(self) -> None:
        import engine_bridge
        result = engine_bridge._reconcile_project_operations(self.tmp)
        self.assertTrue(result["ok"])
        self.assertFalse((self.tmp / "meetingvar").exists())

    def test_bridge_load_status_and_project_entry_reconcile_only_requested_project(self) -> None:
        import contextlib
        import io
        import engine_bridge
        cases = [("agent_chat_load", "cmd_agent_chat_load", ["thread"]),
                 ("chat_action_status", "cmd_chat_action_status", ["thread", "action"]),
                 ("agent_status", "cmd_agent_status", []),
                 ("scan_project", "cmd_scan_project", [])]
        for command, handler, extra in cases:
            with self.subTest(command=command), \
                 mock.patch.object(sys, "argv", ["engine_bridge.py", command, str(self.tmp), *extra]), \
                 mock.patch.object(engine_bridge, "_reconcile_project_operations", return_value={"ok": True}) as recover, \
                 mock.patch.object(engine_bridge, handler, return_value={"ok": True}), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                engine_bridge.main()
                recover.assert_called_once_with(self.tmp)
                self.assertTrue(json.loads(output.getvalue())["operation_recovery"]["ok"])

    def test_finalization_without_durable_status_stays_unverified(self) -> None:
        with mock.patch.object(self.b, "_agent_bridge", return_value={"ok": True}):
            result = self.b._finalize_chat_action(str(self.tmp), "thread", "action", {"ok": True})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unverified")
        self.assertTrue(result["journal_pending"])

    def test_startup_configuration_error_precedes_journal(self) -> None:
        self.b._cfg.update(engine_python=sys.executable, interest_mode="api", llm_base_url="", llm_model="")
        with mock.patch.object(self.b, "_host_operation_begin") as begin:
            result = self.b.start_meeting("", "record")
        self.assertIn("不会退回热词", result["error"])
        begin.assert_not_called()

    def test_bridge_mutations_use_executor_owner_but_host_keeps_desktop_owner(self) -> None:
        from types import SimpleNamespace
        self.b._cfg.update(engine_python=sys.executable, repo_dir=str(self.tmp))
        completed = SimpleNamespace(stdout='{"ok":true}', returncode=0)
        with mock.patch.object(desktop_app.subprocess, "run", return_value=completed) as run:
            self.b._agent_bridge(["kg_build", str(self.tmp)])
            mutation_env = run.call_args.kwargs["env"]
            self.assertNotIn("MEETINGVAR_OPERATION_OWNER_PID", mutation_env)
            self.assertNotIn("MEETINGVAR_OPERATION_OWNER_START", mutation_env)
            self.assertIn("MEETINGVAR_OPERATION_ID", mutation_env)
            self.b._agent_bridge(["host_operation_begin", str(self.tmp), "host.manual_var", "op", "payload"])
            if os.name == "nt":
                self.assertEqual(run.call_args.kwargs["env"]["MEETINGVAR_OPERATION_OWNER_PID"], str(os.getpid()))

    def test_stream_abnormal_or_invalid_result_interrupts_exact_run(self) -> None:
        import io
        self.b._cfg.update(engine_python=sys.executable, repo_dir=str(self.tmp))
        for output, code, expected in [("{broken\n", 0, "invalid_result"),
                                       ('{"ok":true}\n', 1, "process_exit_error"),
                                       ("", 0, "process_exited_without_result")]:
            identity = {"folder": str(self.tmp), "conversation_id": "thread", "run_id": expected}
            process = mock.Mock()
            process.stdout = io.StringIO(output)
            process.wait.return_value = code
            process.poll.return_value = code
            with self.subTest(expected=expected), \
                 mock.patch.object(desktop_app.subprocess, "Popen", return_value=process), \
                 mock.patch.object(self.b, "_agent_bridge", return_value={"ok": True}) as bridge:
                result = self.b._agent_bridge_stream([], identity=identity)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], expected)
            bridge.assert_called_once_with(["chat_interrupt", str(self.tmp), expected, expected], timeout=30)
            self.assertIsNone(self.b._chat_identity)

    def test_stream_cancellation_interrupts_original_run_after_exit(self) -> None:
        self.b._cfg.update(engine_python=sys.executable, repo_dir=str(self.tmp))
        identity = {"folder": str(self.tmp), "conversation_id": "thread", "run_id": "cancel-run"}
        process = mock.Mock()
        process.poll.return_value = None
        def output():
            self.assertTrue(self.b.chat_cancel(**identity)["ok"])
            process.poll.return_value = 1
            yield '{"ok":true}\n'
        process.stdout = output()
        process.wait.return_value = 1
        with mock.patch.object(desktop_app.subprocess, "Popen", return_value=process), \
             mock.patch.object(self.b, "_agent_bridge", return_value={"ok": True}) as bridge:
            result = self.b._agent_bridge_stream([], identity=identity)
        self.assertEqual(result["error_code"], "cancelled")
        process.kill.assert_called_once()
        bridge.assert_called_once_with(["chat_interrupt", str(self.tmp), "cancel-run", "cancelled"], timeout=30)
        self.assertIsNone(self.b._chat_identity)

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
