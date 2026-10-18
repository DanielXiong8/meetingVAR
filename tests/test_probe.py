"""全库分层阅读（probe）测试（model-free）：去重/分块合并/块内校验/确定性合并/缓存/覆盖率。

背景实测（2026-10-16）：jieba 频率统计在"小说类语料"上失效（主角名全缺席），
且此前实现只读到全库开头 0.4% —— 本模块保证"全库每块都被读到、覆盖率可核算"。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.memory import probe  # noqa: E402

BODY = "温水说了一句话。" * 150  # 1200 字
DOC_A = "第一卷 序\n" + BODY + "\n第一败 转学生\n" + BODY + "\n特典 短篇\n" + BODY


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_probe_"))
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

    def _fake_chat(self, calls: list):
        def fake(prompt, temperature=0.3, timeout=900):
            calls.append(prompt)
            if "标题清单" in prompt:
                return "这是一套测试文档集。"
            return json.dumps({
                "persons": [{"name": "温水", "role": "主角"}, {"name": "查无此人", "role": "编造"}],
                "keywords": [{"term": "温水", "why": "主角名"}, {"term": "编造词", "why": "不在原文"}],
                "events": [{"what": "一句话事件", "quote": "温水说了一句话。"}],
            }, ensure_ascii=False)
        return fake

    def test_understand_merge_cache_and_verify(self) -> None:
        import meetingvar.llm as llm_mod

        calls: list = []
        with mock.patch.object(llm_mod, "chat", self._fake_chat(calls)):
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
        with mock.patch.object(llm_mod, "chat", self._fake_chat(calls2)):
            r2 = probe.understand(self.tmp, chunk_chars=800)
        self.assertEqual(r2["cache_hits"], r2["chunks_read"])
        self.assertEqual(len(calls2), 1, f"缓存命中后只应剩概述调用（实际 {len(calls2)} 次）")
        self.assertEqual(r2["terms"], r1["terms"])
        self.assertGreater(n_first, 1)

    def test_understand_max_chunks_partial_coverage(self) -> None:
        import meetingvar.llm as llm_mod

        with mock.patch.object(llm_mod, "chat", self._fake_chat([])):
            r = probe.understand(self.tmp, max_chunks=1, chunk_chars=600)
        self.assertEqual(r["chunks_read"], 1)
        self.assertLess(r["coverage_pct"], 100.0)

    def test_understand_llm_down_is_honest(self) -> None:
        import meetingvar.llm as llm_mod

        with mock.patch.object(llm_mod, "chat", lambda *a, **k: None):
            r = probe.understand(self.tmp, chunk_chars=800)
        self.assertFalse(r["ok"])
        self.assertIn("模型无输出", r["error"] or "")

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