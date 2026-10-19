"""全库分层阅读（probe）测试（model-free）：去重/分块合并/块内校验/确定性合并/缓存/覆盖率。

背景实测（2026-10-16）：jieba 频率统计在"小说类语料"上失效（主角名全缺席），
且此前实现只读到全库开头 0.4% —— 本模块保证"全库每块都被读到、覆盖率可核算"。
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

# No real cwd/.env is opened, including when this module runs on its own.
with mock.patch("meetingvar.env.load_local_env"):
    from meetingvar import config
    from meetingvar import llm
    from meetingvar.memory import probe
import engine_bridge

BODY = "温水说了一句话。" * 150  # 1200 字
DOC_A = "第一卷 序\n" + BODY + "\n第一败 转学生\n" + BODY + "\n特典 短篇\n" + BODY


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="mv_probe_")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        settings = mock.patch.multiple(config, LLM_BASE_URL="https://example.invalid/v1",
                                       LLM_MODEL="synthetic-test", LLM_API_KEY="test-only-secret",
                                       LLM_MAX_TOKENS="4096", LLM_CONTEXT_TOKENS="16384",
                                       LLM_THINKING="", create=True)
        settings.start()
        self.addCleanup(settings.stop)
        chunk_env = mock.patch.dict(os.environ, {"MEETINGVAR_LLM_CHUNK_CHARS": "6000"})
        chunk_env.start()
        self.addCleanup(chunk_env.stop)
        for function in ("urlopen", "build_opener"):
            blocker = mock.patch("meetingvar.llm.urllib.request." + function,
                                 side_effect=AssertionError("unexpected network operation"))
            blocker.start()
            self.addCleanup(blocker.stop)
        (self.tmp / "a.txt").write_text(DOC_A, encoding="utf-8")
        # 副本只在尾部多两句（真实场景：副本与正本前 2 万字逐字节相同）——前缀包含/前缀全等必须抓到
        (self.tmp / "b_copy.txt").write_text(DOC_A + "\n补记：尾声。\n", encoding="utf-8")
        (self.tmp / "bin.txt").write_bytes(b"PK\x03\x04" + b"\xd2" * 200)

    def test_dedupe_by_prefix_containment(self) -> None:
        docs, notes = probe.load_corpus(self.tmp)
        self.assertEqual([n for n, _ in docs], ["a.txt"], "近似副本应被去重；二进制应跳过")
        self.assertTrue(any("重复" in n for n in notes))
        self.assertTrue(any("跳过" in n for n in notes))

    def test_meetingvar_archive_excluded(self) -> None:
        """<项目>/meetingvar/ 是引擎归档区，不得进语料（否则确认词表会被自己读一遍）。"""
        archive = self.tmp / "meetingvar"
        archive.mkdir()
        (archive / "terms.txt").write_text("#!keep 自己写的词表\n", encoding="utf-8")
        (archive / "minutes.md").write_text("本场纪要内容若干\n", encoding="utf-8")
        docs, _ = probe.load_corpus(self.tmp)
        self.assertEqual([n for n, _ in docs], ["a.txt"], "归档区文件必须被排除")

    def test_tiny_duplicates_are_not_reported(self) -> None:
        """3 字的'重复文件'是噪音（实测 docx 小附件一箩筐）：小于 200 字不去重、不报告。"""
        (self.tmp / "tiny1.txt").write_text("同上\n", encoding="utf-8")
        (self.tmp / "tiny2.txt").write_text("同上\n", encoding="utf-8")
        docs, notes = probe.load_corpus(self.tmp)
        names = [n for n, _ in docs]
        self.assertIn("tiny1.txt", names)
        self.assertIn("tiny2.txt", names)
        self.assertFalse(any("tiny" in n and "重复" in n for n in notes))

    def test_plan_notes_stats_and_summary(self) -> None:
        """预算行只给摘要（去重/跳过各几组），明细在 notes 里——曾把几十条说明糊一墙。"""
        p = probe.plan(self.tmp)
        st = p["notes_stats"]
        self.assertEqual(st["skipped"], 1, "bin.txt 应计 1 个跳过")
        self.assertEqual(st["dups"], 1, "b_copy.txt 应计 1 组重复")
        self.assertIn("跳过 1 个不可读文件", p["notes_summary"])
        self.assertIn("合并重复 1 组", p["notes_summary"])

    def test_headings_need_re_multiline_and_labels(self) -> None:
        docs, _ = probe.load_corpus(self.tmp)
        chunks = probe.split_chunks(docs, chunk_chars=800)
        labels = {c["label"] for c in chunks}
        self.assertTrue(any(l.startswith("第一败") for l in labels),
                        f"第X败/特典 标题必须被 re.M 匹配到（实际: {labels}）")

    def test_small_chunks_coalesced_and_bounded(self) -> None:
        docs = [("big.txt", DOC_A)]
        fine = probe.split_chunks(docs, chunk_chars=300)
        coarse = probe.split_chunks(docs, chunk_chars=probe.CHUNK_CHARS)
        self.assertLess(len(coarse), len(fine), "相邻碎片块应被合并")
        self.assertTrue(all(len(c["text"]) <= probe.CHUNK_CHARS for c in coarse))

    def test_plan_coverage_100pct(self) -> None:
        p = probe.plan(self.tmp)
        # 与盘上字节比（write_text 在 Windows 把 \n 翻成 \r\n；而 read_text 会再翻回去，
        # 引擎走的是 read_text_safely=按字节解码——三者别混，实测踩过）
        on_disk = (self.tmp / "a.txt").read_bytes().decode("utf-8")
        self.assertEqual(p["chars"], len(on_disk))
        self.assertGreater(p["est_local_minutes"], 0)
        self.assertLessEqual(sum(c["chars"] for c in p["chunks"]), p["chars"])

    def test_verify_chunk_drops_hallucinations(self) -> None:
        text = "温水说了一句话。八奈见在旁边。"
        data = {"persons": [{"name": "温水", "role": "主角"}, {"name": "不存在的人", "role": "x"}],
                "keywords": [{"term": "八奈见", "why": "w"}, {"term": "编造词", "why": "y"}],
                "events": [{"what": "对话", "quote": "温水说了一句话。"},
                           {"what": "幽灵事件", "quote": "这段原文里没有"}]}
        out = probe.verify_chunk(data, text)
        self.assertEqual([p["name"] for p in out["persons"]], ["温水"])
        self.assertEqual([k["term"] for k in out["keywords"]], ["八奈见"])
        self.assertEqual(len(out["events"]), 2)
        self.assertEqual(out["events"][1]["quote"], "", "引文不在块内→清空但保留事件")

    @staticmethod
    def _reply(content):
        return {"ok": True, "content": content, "error": None, "error_code": None,
                "retryable": False, "finish_reason": "stop", "usage": {}}

    def _fake_chat(self, calls: list):
        def fake(prompt, temperature=0.3, timeout=900):
            calls.append(prompt)
            if "标题清单" in prompt:
                return self._reply("这是一套测试文档集。")
            return self._reply(json.dumps({
                "persons": [{"name": "温水", "role": "主角"}, {"name": "查无此人", "role": "编造"}],
                "keywords": [{"term": "温水", "why": "主角名"}, {"term": "编造词", "why": "不在原文"}],
                "events": [{"what": "一句话事件", "quote": "温水说了一句话。"}],
            }, ensure_ascii=False))
        return fake

    def test_understand_merge_cache_and_verify(self) -> None:
        import meetingvar.llm as llm_mod

        calls: list = []
        with mock.patch.object(llm_mod, "chat_result", self._fake_chat(calls)):
            r1 = probe.understand(self.tmp, chunk_chars=800)
        self.assertTrue(r1["ok"], r1.get("error"))
        self.assertGreaterEqual(r1["chunks_read"], 2)
        self.assertEqual(r1["coverage_pct"], 100.0)
        terms = {t["term"]: t for t in r1["terms"]}
        self.assertIn("温水", terms, "真词必须保留")
        self.assertNotIn("编造词", terms, "块内不存在的词必须被本地校验丢弃")
        self.assertNotIn("查无此人", [p["name"] for p in r1["persons"]])
        self.assertEqual(terms["温水"]["count"], DOC_A.count("温水"), "计数=全库精确出现次数")
        self.assertTrue(r1["overview"].startswith("这是一套"))
        n_first = len(calls)

        # 第二遍：块全走缓存（只剩 overview 一次调用），且覆盖率不变
        calls2: list = []
        with mock.patch.object(llm_mod, "chat_result", self._fake_chat(calls2)):
            r2 = probe.understand(self.tmp, chunk_chars=800)
        self.assertEqual(r2["cache_hits"], r2["chunks_read"])
        self.assertEqual(len(calls2), 1, f"缓存命中后只应剩概述调用（实际 {len(calls2)} 次）")
        self.assertEqual(r2["terms"], r1["terms"])
        self.assertGreater(n_first, 1)

    def test_understand_max_chunks_partial_coverage(self) -> None:
        import meetingvar.llm as llm_mod

        with mock.patch.object(llm_mod, "chat_result", self._fake_chat([])):
            r = probe.understand(self.tmp, max_chunks=1, chunk_chars=600)
        self.assertEqual(r["chunks_read"], 1)
        self.assertLess(r["coverage_pct"], 100.0)

    def test_understand_llm_down_is_honest(self) -> None:
        import meetingvar.llm as llm_mod

        with mock.patch.object(llm_mod, "chat_result", return_value=llm.error_result("network")):
            r = probe.understand(self.tmp, chunk_chars=800)
        self.assertFalse(r["ok"])
        self.assertFalse(r["complete"])
        self.assertEqual(r["error_code"], "network")
        self.assertIn("读取失败", r["error"] or "")

    def test_plan_and_execution_share_budget_for_mixed_text(self):
        docs = [("nested/混合.txt", "第一卷\n" + "温水🙂ABC " * 2500 + "\n尾声\n" + "Z" * 1000)]
        for context, output in ((8192, 2048), (16384, 4096), (32768, 4096)):
            with self.subTest(context=context), mock.patch.multiple(
                    config, LLM_CONTEXT_TOKENS=str(context), LLM_MAX_TOKENS=str(output)), \
                    mock.patch.object(probe, "load_corpus", return_value=(docs, [])):
                budget = llm.token_budget()
                planned = probe.plan(self.tmp)
                calls = []
                with mock.patch.object(llm, "chat_result", self._fake_chat(calls)):
                    result = probe.understand(self.tmp, overview=False)
                self.assertTrue(result["complete"])
                self.assertEqual(result["coverage_pct"], 100.0)
                self.assertEqual(result["chunks_total"], len(planned["chunks"]))
                self.assertEqual(len(calls), result["chunks_total"])
                self.assertTrue(all(budget.fits(prompt) for prompt in calls))
                self.assertTrue(all(ch["chars"] <= 6000 for ch in planned["chunks"]))
                self.assertEqual(planned["budget"]["method"], "utf8_bytes_upper_bound")
                self.assertTrue(any("不是精确" in w for w in result["warnings"]))
                chunks = probe.split_chunks(docs, budget=budget)
                self.assertEqual("".join(ch["text"] for ch in chunks), docs[0][1])
                self.assertEqual(sum(len(ch["text"]) for ch in chunks), len(docs[0][1]))

    def test_chunk_cap_override_and_budget_rejection_are_offline(self):
        with mock.patch.dict(os.environ, {"MEETINGVAR_LLM_CHUNK_CHARS": "300"}):
            planned = probe.plan(self.tmp)
            self.assertTrue(all(c["chars"] <= 300 for c in planned["chunks"]))
        for key, value, code in (("MEETINGVAR_LLM_CHUNK_CHARS", "0", "invalid_config"),
                                 ("MEETINGVAR_LLM_CHUNK_CHARS", "no", "invalid_config")):
            with mock.patch.dict(os.environ, {key: value}):
                self.assertEqual(probe.plan(self.tmp)["error_code"], code)
                self.assertEqual(probe.understand(self.tmp)["error_code"], code)
        with mock.patch.object(config, "LLM_CONTEXT_TOKENS", "4096"):
            result = engine_bridge.cmd_understand_plan(self.tmp)
            self.assertEqual(result["error_code"], "context_budget")
            self.assertNotIn("没有可读", result["error"])

    def test_invalid_schema_does_not_enter_cache(self):
        bad = ('{}', '[]', '{"persons":[null],"keywords":[],"events":[]}',
               '{"persons":{},"keywords":[],"events":[]}',
               '{"persons":[],"keywords":[{"term":1,"why":"x"}],"events":[]}',
               'explanation {"persons":[],"keywords":[],"events":[]}',
               '{"persons":[],"persons":[],"keywords":[],"events":[]}')
        for text in bad:
            with self.subTest(text=text), mock.patch.object(llm, "chat_result", return_value=self._reply(text)):
                result = probe.understand(self.tmp, overview=False)
                self.assertEqual(result["error_code"], "invalid_json")
                self.assertEqual(result["chunks_read"], 0)
                self.assertFalse(result["complete"])
                self.assertEqual(list(probe._cache_dir(self.tmp).glob("*.json")), [])

    def test_single_trailing_quote_artifact_is_tolerated(self):
        payload = json.dumps({
            "persons": [{"name": "温水", "role": "主角"}],
            "keywords": [{"term": "温水", "why": "主角名"}],
            "events": [{"what": "说了一句话", "quote": "温水说了一句话。"}],
        }, ensure_ascii=False) + '"'
        with mock.patch.object(llm, "chat_result", return_value=self._reply(payload)):
            result = probe.understand(self.tmp, overview=False)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["complete"])

    def test_unescaped_inner_quotes_are_repaired(self):
        raw = ('{"persons":[],"keywords":[],"events":[{"what":"课程电子化",'
               '"quote":"This course attempts to be "paperless" as much as possible!"}]}')
        docs = [("course.txt", 'This course attempts to be "paperless" as much as possible!')]
        with mock.patch.object(probe, "load_corpus", return_value=(docs, [])), \
             mock.patch.object(llm, "chat_result", return_value=self._reply(raw)):
            result = probe.understand(self.tmp, overview=False)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["complete"])
        self.assertTrue(result["events"][0]["quote"].startswith(
            'This course attempts to be "paperless"'))

    def test_invalid_json_is_retried_once_before_failing(self):
        good = json.dumps({"persons": [], "keywords": [],
                           "events": [{"what": "说了一句话", "quote": "温水说了一句话。"}]},
                          ensure_ascii=False)
        calls = []

        def fake(prompt, **kwargs):
            calls.append(prompt)
            return self._reply("不是 JSON" if len(calls) == 1 else good)

        with mock.patch.object(probe, "load_corpus", return_value=([("one.txt", "温水说了一句话。")], [])), \
             mock.patch.object(llm, "chat_result", side_effect=fake):
            result = probe.understand(self.tmp, overview=False)
        self.assertEqual(len(calls), 2)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["complete"])
        self.assertTrue(any("自动重试" in w for w in result["warnings"]))

    def test_valid_empty_arrays_are_successful_reading(self):
        with mock.patch.object(llm, "chat_result", return_value=self._reply(
                '{"persons":[],"keywords":[],"events":[]}')):
            result = probe.understand(self.tmp)
        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["terms"], [])
        self.assertEqual(result["coverage_pct"], 100.0)

    def test_persistent_failure_falls_back_to_compact_keywords(self):
        good = json.dumps({"persons": [], "keywords": [{"term": "温水", "why": "主角名"}],
                           "events": []}, ensure_ascii=False)
        calls = []

        def fake(prompt, **kwargs):
            calls.append(prompt)
            return self._reply(good if len(calls) == 4 else "不是 JSON")

        with mock.patch.object(probe, "load_corpus", return_value=([("one.txt", "温水说了一句话。")], [])), \
             mock.patch.object(llm, "chat_result", side_effect=fake):
            result = probe.understand(self.tmp, overview=False)
        self.assertEqual(len(calls), 4)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["complete"])
        self.assertTrue(any("精简模式" in w for w in result["warnings"]))

    def test_recoverable_chunk_failure_continues_and_reports_partial(self):
        calls = []

        def first_chunk_bad(prompt, **kwargs):
            calls.append(prompt)
            if len(calls) <= 4:
                return self._reply("不是 JSON")
            return self._reply(json.dumps({
                "persons": [], "keywords": [{"term": "温水", "why": "主角名"}],
                "events": [{"what": "说了一句话", "quote": "温水说了一句话。"}],
            }, ensure_ascii=False))

        with mock.patch.object(llm, "chat_result", first_chunk_bad):
            result = probe.understand(self.tmp, chunk_chars=800, overview=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "partial_read")
        self.assertTrue(result["failed_chunks"])
        self.assertGreater(result["chunks_read"], 0)
        self.assertTrue(result["terms"])

    def test_second_chunk_failure_keeps_first_cache_and_truthful_progress(self):
        calls, progress = [], []
        good = self._fake_chat(calls)

        def fail_second(prompt, **kwargs):
            if calls:
                return llm.error_result("auth")
            return good(prompt, **kwargs)

        with mock.patch.object(llm, "chat_result", fail_second):
            result = probe.understand(self.tmp, chunk_chars=800, overview=False, progress=progress.append)
        self.assertEqual(result["chunks_read"], 1)
        self.assertEqual(result["error_code"], "auth")
        self.assertFalse(result["complete"])
        self.assertTrue(result["terms"])
        self.assertTrue(progress[0].startswith("处理中"))
        self.assertEqual(sum(p.startswith("已读") for p in progress), 1)
        self.assertEqual(sum(p.startswith("处理中") for p in progress), 2)
        self.assertEqual(len(list(probe._cache_dir(self.tmp).glob("*.json"))), 1)
        resumed = []
        with mock.patch.object(llm, "chat_result", self._fake_chat(resumed)):
            result = probe.understand(self.tmp, chunk_chars=800, overview=False)
        self.assertTrue(result["complete"])
        self.assertEqual(result["cache_hits"], 1)
        self.assertEqual(len(resumed), result["chunks_total"] - 1)

    def test_empty_overview_is_a_warning_not_a_failed_reading(self):
        fake = self._fake_chat([])

        def empty_overview(prompt, **kwargs):
            return llm.error_result("empty_content") if "标题清单" in prompt else fake(prompt, **kwargs)

        with mock.patch.object(llm, "chat_result", empty_overview):
            result = probe.understand(self.tmp)
        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])
        self.assertIsNone(result["error_code"])
        self.assertEqual(result["overview"], "")
        self.assertTrue(any("概述未生成（empty_content）" in w for w in result["warnings"]))

    def test_cache_isolates_endpoint_full_model_budget_and_thinking(self):
        baseline = {"LLM_BASE_URL": "https://example.invalid/v1", "LLM_MODEL": "same-prefix-long-name-A",
                    "LLM_MAX_TOKENS": "4096", "LLM_CONTEXT_TOKENS": "16384", "LLM_THINKING": ""}
        variants = ({}, {"LLM_BASE_URL": "https://other.invalid/v1"},
                    {"LLM_MODEL": "same-prefix-long-name-B"}, {"LLM_MAX_TOKENS": "2048"},
                    {"LLM_CONTEXT_TOKENS": "32768"}, {"LLM_THINKING": "disabled"})
        for variant in variants:
            with self.subTest(variant=variant), mock.patch.multiple(config, **(baseline | variant)):
                calls = []
                with mock.patch.object(llm, "chat_result", self._fake_chat(calls)):
                    result = probe.understand(self.tmp, overview=False)
                self.assertTrue(result["complete"])
                self.assertEqual(result["cache_hits"], 0)
                self.assertTrue(calls)
                with mock.patch.object(llm, "chat_result", side_effect=AssertionError("cache miss")):
                    reused = probe.understand(self.tmp, overview=False)
                self.assertEqual(reused["cache_hits"], reused["chunks_total"])

    def test_cache_prompt_and_relative_document_identity(self):
        for name, version in (("a/same.txt", "v2"), ("b/same.txt", "v2"), ("b/same.txt", "v3")):
            with self.subTest(name=name, version=version), \
                    mock.patch.object(probe, "load_corpus", return_value=([(name, DOC_A)], [])), \
                    mock.patch.object(probe, "PROMPT_VERSION", version), \
                    mock.patch.object(llm, "chat_result", self._fake_chat([])):
                result = probe.understand(self.tmp, overview=False)
                self.assertEqual(result["cache_hits"], 0)
                self.assertTrue(result["complete"])
        nested = self.tmp / "nested"
        nested.mkdir()
        (nested / "a.txt").write_text("a distinct short synthetic document", encoding="utf-8")
        docs, _ = probe.load_corpus(self.tmp)
        self.assertIn("nested/a.txt", [name for name, _ in docs])

    def test_old_cache_is_retained_and_corrupt_v2_is_recomputed(self):
        legacy = probe._cache_dir(self.tmp).parent / "legacy_model.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"legacy-sentinel")
        with mock.patch.object(llm, "chat_result", self._fake_chat([])):
            first = probe.understand(self.tmp, overview=False)
        self.assertEqual(first["cache_hits"], 0)
        self.assertEqual(legacy.read_bytes(), b"legacy-sentinel")
        cache = next(probe._cache_dir(self.tmp).glob("*.json"))
        envelope = json.loads(cache.read_text(encoding="utf-8"))
        envelope["result"] = ["bad shape"]
        cache.write_text(json.dumps(envelope), encoding="utf-8")
        calls = []
        with mock.patch.object(llm, "chat_result", self._fake_chat(calls)):
            result = probe.understand(self.tmp, overview=False)
        self.assertTrue(result["complete"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["cache_hits"], result["chunks_total"] - 1)
        self.assertTrue(any("结构无效" in w for w in result["warnings"]))

    def test_cache_revalidates_text_evidence(self):
        with mock.patch.object(llm, "chat_result", self._fake_chat([])):
            probe.understand(self.tmp, overview=False)
        for cache in probe._cache_dir(self.tmp).glob("*.json"):
            envelope = json.loads(cache.read_text(encoding="utf-8"))
            envelope["result"]["keywords"].append({"term": "缓存编造词", "why": "bad"})
            cache.write_text(json.dumps(envelope), encoding="utf-8")
        with mock.patch.object(llm, "chat_result", side_effect=AssertionError("unexpected generation")):
            result = probe.understand(self.tmp, overview=False)
        self.assertNotIn("缓存编造词", [item["term"] for item in result["terms"]])
        self.assertEqual(result["cache_hits"], result["chunks_total"])

    def test_bridge_preserves_context_on_failure_partial_and_sample(self):
        out = self.tmp / "meetingvar" / "interest_context.json"
        out.parent.mkdir()
        old = b'{"overview":"last successful context"}'
        out.write_bytes(old)
        for ok, complete, sample in ((False, False, 0), (True, False, 0), (True, True, 1)):
            with self.subTest(ok=ok, complete=complete, sample=sample), \
                    mock.patch.object(probe, "understand", return_value={"ok": ok, "complete": complete}):
                engine_bridge.cmd_understand(self.tmp, sample)
                self.assertEqual(out.read_bytes(), old)
        out.unlink()
        with mock.patch.object(probe, "understand", return_value={"ok": True, "complete": True}):
            engine_bridge.cmd_understand(self.tmp, 1)
        self.assertFalse(out.exists(), "sample should not publish even when no prior context exists")

    def test_bridge_complete_context_is_atomic_and_replace_failure_preserves_old(self):
        out = self.tmp / "meetingvar" / "interest_context.json"
        out.parent.mkdir()
        old = b'{"overview":"old"}'
        out.write_bytes(old)
        completed = {"ok": True, "complete": True, "overview": "new", "terms": [],
                     "coverage_pct": 100.0, "error": None, "error_code": None, "warnings": []}
        with mock.patch.object(probe, "understand", return_value=dict(completed)), \
                mock.patch.object(probe.os, "replace", side_effect=OSError("sensitive path")):
            result = engine_bridge.cmd_understand(self.tmp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "context_write_failed")
        self.assertNotIn("sensitive", json.dumps(result))
        self.assertEqual(out.read_bytes(), old)
        self.assertEqual(list(out.parent.glob("*.tmp")), [])
        with mock.patch.object(probe, "understand", return_value=dict(completed)):
            result = engine_bridge.cmd_understand(self.tmp)
        self.assertTrue(result["ok"])
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["overview"], "new")
        self.assertEqual(list(out.parent.glob("*.tmp")), [])

    def test_write_terms_and_go_pickup(self) -> None:
        """确认词写 <项目>/meetingvar/terms.txt（#!keep）→ go 载入函数必须能读到。"""
        from meetingvar.go import load_confirm_terms

        out = probe.write_terms(self.tmp, ["八奈见杏菜", "文艺部", "八奈见杏菜"])
        self.assertTrue(out.exists())
        text = out.read_text(encoding="utf-8")
        self.assertEqual(text.count("#!keep 八奈见杏菜"), 1, "重复词只写一次")
        self.assertIn("#!keep 文艺部", text)
        self.assertEqual(load_confirm_terms(self.tmp), ["八奈见杏菜", "文艺部"])


if __name__ == "__main__":
    unittest.main()