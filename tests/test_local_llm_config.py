"""本地服务配置的 model-free 回归测试；文件发现全部 mock，不启动进程。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

import local_llm  # noqa: E402


def option(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


class LaunchConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "__mock_llm__"
        self.model = str(self.root / "private model.gguf")
        self.python = str(self.root / "python.exe")
        self.server = str(self.root / "llama-server.exe")
        self.files = {self.model, self.python, self.server}
        self.config = {"llm_gguf": self.model, "engine_python": self.python}
        self.file_check = self.enter_patch(
            mock.patch.object(local_llm, "_is_file", side_effect=lambda p: str(p) in self.files))
        self.enter_patch(mock.patch.object(local_llm, "_is_dir", return_value=False))
        self.discovery = self.enter_patch(mock.patch.object(local_llm, "discover_server", return_value=""))
        self.enter_patch(mock.patch("subprocess.Popen", side_effect=AssertionError("must not start a process")))
        self.enter_patch(mock.patch("subprocess.run", side_effect=AssertionError("must not run a command")))

    def enter_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def build(self, **updates):
        return local_llm.build_launch({**self.config, **updates}, 11439)

    def test_auto_falls_back_to_cpu_with_explicit_memory_guards(self) -> None:
        launch = self.build()
        cmd = launch["cmd"]
        self.assertEqual(launch["backend"], "python")
        self.assertEqual(launch["context_tokens"], 8192)
        self.assertEqual(option(cmd, "--logits_all"), "false")
        self.assertEqual(option(cmd, "--n_gpu_layers"), "0")
        self.assertEqual(option(cmd, "--n_batch"), "256")
        self.assertIn("CPU", launch["summary"])
        self.assertNotIn("GPU", launch["summary"])
        self.assertEqual(launch["model_alias"], self.model)
        self.assertEqual(cmd[:3], [self.python, "-m", "llama_cpp.server"])

    def test_cpu_scores_allocation_is_bounded_independently_of_context(self) -> None:
        """真实回归条件：服务默认 logits_all=True 曾导致 32K 的 scores 达 18.55 GiB。"""
        allocations = []
        for requested in (2048, 8192, 16384, 32768):
            with self.subTest(context=requested):
                launch = self.build(llm_backend="python", llm_context_tokens=requested,
                                    llm_gpu_layers=-1, llm_device="Vulkan0")
                cmd = launch["cmd"]
                context = int(option(cmd, "--n_ctx"))
                batch = int(option(cmd, "--n_batch"))
                self.assertLessEqual(context, 8192)
                self.assertEqual(option(cmd, "--logits_all"), "false")
                self.assertEqual(option(cmd, "--n_gpu_layers"), "0")
                self.assertNotIn("--device", cmd)
                rows = context if option(cmd, "--logits_all") == "true" else batch
                allocations.append(rows * 151936 * 4)  # Qwen3 vocab × float32
        self.assertEqual(len(set(allocations)), 1)
        self.assertLess(allocations[0], 160 * 1024**2)
        self.discovery.assert_not_called()

    def test_native_defaults_are_explicit_and_non_thinking(self) -> None:
        self.discovery.return_value = self.server
        launch = self.build()
        cmd = launch["cmd"]
        self.assertEqual(launch["backend"], "native")
        self.assertEqual(launch["context_tokens"], 16384)
        for flag, value in {
                "--ctx-size": "16384", "--n-gpu-layers": "all", "--parallel": "1",
                "--batch-size": "512", "--ubatch-size": "256", "--flash-attn": "on",
                "--cache-type-k": "q8_0", "--cache-type-v": "q8_0",
                "--alias": "meetingvar-local"}.items():
            self.assertEqual(option(cmd, flag), value)
        self.assertIn("--jinja", cmd)
        self.assertIn("--no-webui", cmd)
        self.assertNotIn("--device", cmd, "不猜测 GPU 设备")
        self.assertEqual(json.loads(option(cmd, "--chat-template-kwargs")), {"enable_thinking": False})
        self.assertEqual(launch["model_alias"], "meetingvar-local")
        self.assertNotIn(self.model, launch["summary"])

    def test_native_explicit_device_layers_and_full_context(self) -> None:
        launch = self.build(llm_backend="native", llm_server_exe=self.server,
                            llm_context_tokens=32768, llm_gpu_layers=20, llm_device="Vulkan0")
        cmd = launch["cmd"]
        self.assertEqual(option(cmd, "--device"), "Vulkan0")
        self.assertEqual(option(cmd, "--n-gpu-layers"), "20")
        self.assertEqual(option(cmd, "--ctx-size"), "32768")
        self.discovery.assert_not_called()

    def test_both_backends_bind_only_to_loopback(self) -> None:
        for backend in ("native", "python"):
            with self.subTest(backend=backend):
                launch = self.build(llm_backend=backend, llm_server_exe=self.server)
                self.assertEqual(option(launch["cmd"], "--host"), "127.0.0.1")
                self.assertEqual(option(launch["cmd"], "--port"), "11439")
                self.assertEqual(option(launch["cmd"], "--model"), self.model)

    def test_native_does_not_require_python(self) -> None:
        self.config.pop("engine_python")
        self.assertEqual(self.build(llm_backend="native", llm_server_exe=self.server)["backend"], "native")

    def test_native_missing_binary_does_not_fall_back(self) -> None:
        with self.assertRaisesRegex(ValueError, "未找到原生"):
            self.build(llm_backend="native")

    def test_explicit_missing_binary_is_not_silently_ignored(self) -> None:
        for backend in ("auto", "native"):
            with self.subTest(backend=backend), self.assertRaisesRegex(ValueError, "程序文件不存在"):
                self.build(llm_backend=backend, llm_server_exe=str(self.root / "missing.exe"))
        self.discovery.assert_not_called()

    def test_python_missing_interpreter_fails_safely(self) -> None:
        with self.assertRaisesRegex(ValueError, "引擎 Python"):
            self.build(llm_backend="python", engine_python="")

    def test_missing_model_or_wrong_extension_is_rejected(self) -> None:
        for model in ("", str(self.root / "missing.gguf"), str(self.root / "weights.safetensors")):
            with self.subTest(model=model), self.assertRaises(ValueError) as caught:
                self.build(llm_gguf=model)
            self.assertNotIn(str(self.root), str(caught.exception))

    def test_context_requires_integer_in_supported_range(self) -> None:
        for value in (True, False, None, "8192", 8192.0, 0, 2047, 32769):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "上下文长度"):
                self.build(llm_context_tokens=value)

    def test_gpu_layers_requires_integer_in_supported_range(self) -> None:
        for value in (True, None, "-1", -2, 1000):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "GPU 层数"):
                self.build(llm_gpu_layers=value)

    def test_port_requires_integer_in_supported_range(self) -> None:
        for value in (True, None, "11439", 0, 65536):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "服务端口"):
                local_llm.build_launch(self.config, value)

    def test_invalid_backend_device_and_paths_are_rejected(self) -> None:
        for updates in ({"llm_backend": "cuda"}, {"llm_backend": None},
                        {"llm_device": "Vulkan0 --host 0.0.0.0"}, {"llm_device": "--help"},
                        {"llm_device": 0}, {"llm_device": "Vulkan0\nsecret"},
                        {"llm_gguf": "bad\x00.gguf"}, {"llm_server_exe": []},
                        {"repo_dir": str(self.root / "missing")}, {"repo_dir": 0}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.build(**updates)
        with self.assertRaises(ValueError):
            local_llm.build_launch([], 11439)


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "__mock_discovery__"
        self.repo = self.root / "repo"
        self.tools = self.repo / ".tools" / "llama.cpp"
        self.external = self.root / "env" / "llama.cpp"
        self.files = set()
        self.directories = {}
        self.enter_patch(mock.patch.object(local_llm, "_REPO_ROOT", self.repo))
        self.enter_patch(mock.patch.object(local_llm, "_SERVER_ROOT", self.external))
        self.enter_patch(mock.patch.dict(local_llm.os.environ, {"MEETINGVAR_LLAMA_SERVER": ""}))
        self.which = self.enter_patch(mock.patch.object(local_llm.shutil, "which", return_value=None))
        self.enter_patch(mock.patch.object(local_llm, "_is_file", side_effect=lambda p: str(p) in self.files))
        self.enter_patch(mock.patch.object(local_llm, "_is_dir", return_value=True))
        self.iterdir = self.enter_patch(mock.patch.object(
            Path, "iterdir", autospec=True, side_effect=lambda p: iter(self.directories.get(p, []))))
        self.enter_patch(mock.patch.object(Path, "rglob", side_effect=AssertionError("no recursive scanning")))

    def enter_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def add_server(self, directory: Path) -> str:
        name = str(directory / "llama-server.exe")
        self.files.add(name)
        return name

    def test_environment_override_precedes_path_and_directories(self) -> None:
        server = self.add_server(self.root / "override")
        with mock.patch.dict(local_llm.os.environ, {"MEETINGVAR_LLAMA_SERVER": server}):
            self.assertEqual(local_llm.discover_server(), server)
        self.which.assert_not_called()
        self.iterdir.assert_not_called()

    def test_invalid_override_falls_through_to_path(self) -> None:
        server = self.add_server(self.root / "path")
        self.which.return_value = server
        with mock.patch.dict(local_llm.os.environ, {"MEETINGVAR_LLAMA_SERVER": "missing.exe"}):
            self.assertEqual(local_llm.discover_server(), server)
        self.iterdir.assert_not_called()

    def test_project_tool_directory_precedes_external_root(self) -> None:
        server = self.add_server(self.tools)
        self.add_server(self.external)
        self.assertEqual(local_llm.discover_server(str(self.repo)), server)

    def test_external_version_directory_and_bin_are_supported(self) -> None:
        old, new = self.external / "b9999-vulkan-x64", self.external / "b11669-vulkan-x64"
        self.directories[self.external] = [old, new]
        self.add_server(old)
        server = self.add_server(new / "bin")
        self.assertEqual(local_llm.discover_server(), server)

    def test_discovery_does_not_recurse_into_unrelated_directories(self) -> None:
        version = self.external / "b11669"
        self.directories[self.external] = [version]
        self.add_server(version / "nested" / "unrelated")
        self.assertEqual(local_llm.discover_server(), "")
        self.assertEqual([call.args[0] for call in self.iterdir.call_args_list], [self.tools, self.external])

    def test_discovery_limits_version_candidates(self) -> None:
        versions = [self.external / f"b{10000 + i}" for i in range(33)]
        self.directories[self.external] = versions
        self.add_server(versions[0])
        self.assertEqual(local_llm.discover_server(), "")

    def test_unreadable_tool_directory_is_not_fatal(self) -> None:
        self.iterdir.side_effect = PermissionError("private path must not escape")
        self.assertEqual(local_llm.discover_server(), "")


class StartupErrorTests(unittest.TestCase):
    def test_classifies_startup_errors_without_returning_raw_logs(self) -> None:
        cases = [
            ("numpy._core._exceptions._ArrayMemoryError: Unable to allocate 18.5 GiB", "内存不足"),
            ("MemoryError", "内存不足"),
            ("OOM", "内存不足"),
            ("std::bad_alloc", "内存不足"),
            ("CUDA OOM", "显存不足"),
            ("CUDA error: out of memory", "显存不足"),
            ("VK_ERROR_OUT_OF_DEVICE_MEMORY", "显存不足"),
            ("[WinError 10048] Only one usage of each socket address", "端口已被占用"),
            ("OSError: [Errno 98] Address already in use", "端口已被占用"),
            ("error loading model: failed to open GGUF file", "模型文件缺失"),
            ("FileNotFoundError: No such file or directory: private.gguf", "模型文件缺失"),
            ("DLL load failed: missing module", "后端库缺失"),
            ("ModuleNotFoundError: No module named llama_cpp", "后端库缺失"),
            ("vulkan-1.dll was not found", "后端库缺失"),
            ("unknown exception", "请检查后端配置"),
        ]
        secret = "C:/private/person/project API_KEY=secret-value Bearer secret-value"
        for raw, expected in cases:
            with self.subTest(raw=raw):
                plain = local_llm.safe_startup_error(raw)
                private = local_llm.safe_startup_error(raw + "\n" + secret)
                self.assertEqual(private, plain)
                self.assertIn(expected, private)
                self.assertNotIn("secret-value", private)
                self.assertNotIn("C:/private", private)
                self.assertNotIn(raw, private)

    def test_empty_and_non_text_logs_get_generic_message(self) -> None:
        for value in ("", None, [], {}):
            with self.subTest(value=value):
                self.assertEqual(local_llm.safe_startup_error(value), local_llm._GENERIC_ERROR)


if __name__ == "__main__":
    unittest.main()
