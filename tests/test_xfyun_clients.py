"""讯飞客户端纯逻辑测试（无网络）：凭证定位、密钥缺失报错、signa/行解析、段结构。

实网行为已在交付前用 90s 样例做过现场验证（IAT 11s 块 1.4s 出字；LFASR 90s 音频 13s 出 20 行），
这里只钉住回归时最容易破的解析/分类逻辑。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.transcribe import xfyun_cred, xfyun_iat, xfyun_lfasr  # noqa: E402

IAT_ENV = """// 语音听写
APPID=appid_demo
APIKey=key_demo
APISecret=secret_demo
"""
LFASR_ENV = """// 录音文件转写
APPID=appid_demo
SecretKey=lf_secret_demo
"""
RTASR_ENV = """// 实时语音转写
APPID=appid_demo
APIKey=rt_key_demo
"""


class CredScanTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="mv_xfcred_"))
        (self.tmp / "语音听写").mkdir()
        (self.tmp / "语音听写" / "Websocket服务接口认证信息.env").write_text(IAT_ENV, encoding="utf-8")
        (self.tmp / "录音文件转写（标准版）").mkdir()
        (self.tmp / "录音文件转写（标准版）" / "Websocket认证信息.env").write_text(LFASR_ENV, encoding="utf-8")
        (self.tmp / "实时语音转写").mkdir()
        (self.tmp / "实时语音转写" / "Websocket认证服务信息.env").write_text(RTASR_ENV, encoding="utf-8")

    def test_classifies_iat_and_lfasr_not_rtasr(self) -> None:
        found = xfyun_cred.scan(self.tmp)
        kinds = {(Path(h["path"]).parent.name, h["kind"]) for h in found}
        self.assertIn(("语音听写", "iat"), kinds)
        self.assertIn(("录音文件转写（标准版）", "lfasr"), kinds)
        self.assertNotIn("实时语音转写", {Path(h["path"]).parent.name for h in found},
                         "实时语音转写（RTASR）凭证键位不含 APISecret，不得被误认成听写")

    def test_find_by_dir_and_file(self) -> None:
        self.assertIn("语音听写", str(xfyun_cred.find_iat(self.tmp)))
        self.assertIn("录音文件转写", str(xfyun_cred.find_lfasr(self.tmp)))
        f = self.tmp / "语音听写" / "Websocket服务接口认证信息.env"
        self.assertEqual(xfyun_cred.find_iat(f), f)
        self.assertIsNone(xfyun_cred.find_lfasr(self.tmp / "不存在"))


class IatClientTests(unittest.TestCase):
    def test_load_creds_and_missing_keys(self) -> None:
        import tempfile

        p = Path(tempfile.mkdtemp(prefix="mv_iat_")) / "a.env"
        p.write_text(IAT_ENV, encoding="utf-8")
        creds = xfyun_iat.load_creds(p)
        self.assertEqual(creds["APPID"], "appid_demo")
        p2 = p.parent / "bad.env"
        p2.write_text("APPID=x\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            xfyun_iat.load_creds(p2)

    def test_auth_url_shape(self) -> None:
        url = xfyun_iat.ws_url({"APPID": "a", "APIKey": "k", "APISecret": "s"})
        self.assertTrue(url.startswith("wss://iat-api.xfyun.cn/v2/iat?"))
        self.assertIn("authorization=", url)

    def test_extract_segment_and_final(self) -> None:
        import json

        mid = json.dumps({"data": {"status": 1, "result": {"ws": [{"cw": [{"w": "你"}]}], "ls": False}}})
        fin = json.dumps({"data": {"status": 2, "result": {"ws": [{"cw": [{"w": "好"}]}], "ls": True}}})
        self.assertEqual(xfyun_iat._extract(mid), ("你", False))
        self.assertEqual(xfyun_iat._extract(fin), ("好", True))
        self.assertEqual(xfyun_iat._extract("{bad json"), ("", False))

    def test_xfyunasr_segment_shape(self) -> None:
        import tempfile
        from unittest import mock

        p = Path(tempfile.mkdtemp(prefix="mv_iat_")) / "a.env"
        p.write_text(IAT_ENV, encoding="utf-8")
        asr = xfyun_iat.XfyunASR(p)
        self.assertFalse(asr.with_embeddings, "云转写无句级声纹（聚类应退化为 spk?）")
        self.assertFalse(asr.sentence_timestamps, "IAT 无句级时间戳：管线须关重叠窗口")
        pcm = b"\x00" * (16000 * 2 * 2)  # 2s
        fake = mock.AsyncMock(return_value=["你好世界", "第二句长一点"])
        with mock.patch.object(xfyun_iat, "transcribe_chunk_async", new=fake):
            segs = asr.transcribe_pcm16k(pcm)
        self.assertEqual(len(segs), 2, "每条 IAT 消息（语段）应拆成一段")
        self.assertEqual(segs[0]["speaker"], "spk?")
        self.assertEqual(segs[0]["text"], "你好世界")
        self.assertIsNone(segs[0]["emb"])
        # 块内时长按字数比例分摊：2s 两块，字多的第二段占用更长
        self.assertAlmostEqual(segs[0]["start"], 0.0)
        self.assertAlmostEqual(segs[1]["end"], 2.0, places=2)
        self.assertGreater(segs[1]["end"] - segs[1]["start"], segs[0]["end"] - segs[0]["start"])
        with mock.patch.object(xfyun_iat, "transcribe_chunk_async", new=mock.AsyncMock(return_value=[])):
            self.assertEqual(asr.transcribe_pcm16k(pcm), [], "空结果不应产段")

    def test_xfyunasr_accepts_dir(self) -> None:
        import tempfile

        d = Path(tempfile.mkdtemp(prefix="mv_iat_dir_"))
        (d / "语音听写").mkdir()
        (d / "语音听写" / "cred.env").write_text(IAT_ENV, encoding="utf-8")
        asr = xfyun_iat.XfyunASR(d)  # 传文件夹：内部认领语音听写凭证
        self.assertEqual(asr.creds["APPID"], "appid_demo")


class LfasrClientTests(unittest.TestCase):
    def test_signa_variants_differ(self) -> None:
        a = xfyun_lfasr.make_signa("app", "sec", "1700000000", md5_mode="hexdigest")
        b = xfyun_lfasr.make_signa("app", "sec", "1700000000", md5_mode="digest")
        self.assertNotEqual(a, b, "两种 signa 写法必须可区分（实测只有 hexdigest 变体被服务端接受）")

    def test_extract_lines_positions_and_role(self) -> None:
        import json

        def item(bg_ms: int, wb_ticks: int, word: str, role: str) -> dict:
            best = {
                "st": {
                    "bg": str(bg_ms), "rl": role,
                    "rt": [{"ws": [{"wb": str(wb_ticks), "cw": [{"w": word}]}]}],
                }
            }
            return {"json_1best": json.dumps(best, ensure_ascii=False)}

        merged = {"lattice": [item(0, 0, "开场", "1"), item(34000, 500, "回应", "2")]}
        lines = xfyun_lfasr.extract_lines(merged)
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(lines[0][0], 0.0)
        self.assertEqual(lines[0][1], "1")
        # 34s + 500 ticks(10ms) = 39.0s
        self.assertAlmostEqual(lines[1][0], 39.0)
        self.assertEqual(lines[1][2], "回应")
        with self.assertRaises((ValueError, OSError)):
            xfyun_lfasr.load_creds(Path("nonexistent.env"))


if __name__ == "__main__":
    unittest.main()