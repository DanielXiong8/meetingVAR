"""讯飞语音听写（流式版）分块客户端：单块 ≤60s，一次会话出全文。

用途：作为 ASR 后端之一（Session(asr_engine="xfyun")）。本管线按 8s 块喂音频，
每块独立开一个 IAT 会话（无需会话链——链只解决 >60s 的问题）：
先发完所有帧（320ms 一拍）、终帧到达即收工（不等服务端断开）。IAT 每语段一条消息、
无句级时间戳/声纹 → 按消息拆句、块内时长按字数比例分摊（近似），说话人 spk?。

凭证加载自 .env 文件（APPID/APISecret/APIKey），不打印、不落盘。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from email.utils import formatdate
from pathlib import Path

HOST = "iat-api.xfyun.cn"
PATH_ = "/v2/iat"
FRAME_BYTES = 1280  # 40ms @16k mono s16
FRAME_MS = 40
BATCH_FRAMES = 8  # 每 320ms 音频睡一拍：Windows 下 sleep(5ms) 实为 ~15.6ms 计时粒度，逐帧睡太亏
BURST_SLEEP_S = 0.005
COLLECT_TIMEOUT_S = 30.0
WARNED = {"once": False}


def load_creds(path: Path) -> dict:
    creds = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "#")):
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k in {"APPID", "APIKey", "APISecret"} and v:
            creds[k] = v
    missing = {"APPID", "APIKey", "APISecret"} - set(creds)
    if missing:
        raise ValueError(f"讯飞凭证缺失: {sorted(missing)}（文件 {path}）")
    return creds


def ws_url(creds: dict) -> str:
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    origin = f"host: {HOST}\ndate: {date}\nGET {PATH_} HTTP/1.1"
    sig = base64.b64encode(
        hmac.new(creds["APISecret"].encode(), origin.encode(), hashlib.sha256).digest()
    ).decode()
    auth_origin = (
        f'api_key="{creds["APIKey"]}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{sig}"'
    )
    auth = base64.b64encode(auth_origin.encode()).decode()
    q = urllib.parse.urlencode({"authorization": auth, "date": date, "host": HOST})
    return f"wss://{HOST}{PATH_}?{q}"


def _extract(msg: str) -> tuple[str, bool]:
    try:
        d = json.loads(msg)
    except ValueError:
        return "", False
    data = d.get("data") or {}
    r = data.get("result") or {}
    txt = "".join(cw.get("w", "") for w in (r.get("ws") or []) for cw in (w.get("cw") or []))
    return txt, (data.get("status") == 2 or bool(r.get("ls")))


def _ws_connect(url: str, **kw):
    import inspect

    import websockets

    if "proxy" in inspect.signature(websockets.connect).parameters:
        kw["proxy"] = None  # 14+ 默认读系统代理，显式直连
    # 单块一次性连接：拿到最终结果即散伙。默认 close_timeout=10s 且需等对端 close 回帧，
    # 实测量到 20s 里 ~16s 白耗在关闭握手——压到 0.5s；短会话无须心跳。
    kw.setdefault("close_timeout", 0.5)
    kw.setdefault("ping_interval", None)
    return websockets.connect(url, **kw)


async def transcribe_chunk_async(creds: dict, pcm: bytes) -> list[str]:
    """每语段一条消息（逐消息解剖实测）→ 按到达顺序收集各语段文本。"""
    n = (len(pcm) + FRAME_BYTES - 1) // FRAME_BYTES
    texts: list[str] = []
    done = asyncio.Event()
    async with _ws_connect(ws_url(creds), max_size=2**22, open_timeout=10) as ws:
        async def receiver() -> None:
            # status=2/ls=True 的终帧即最后一句；终帧到就返回——服务端之后还会把连接
            # 挂 ~16s 等会话链续帧，不能死等它断开（实测 20.6s → 1.4s 的关键）。
            try:
                async for m in ws:
                    t, d = _extract(m)
                    if t:
                        texts.append(t)
                    if d:
                        return
            except Exception:  # noqa: BLE001 — 服务端句尾主动断开
                pass
            finally:
                done.set()

        rt = asyncio.create_task(receiver())
        try:
            for i in range(n):
                fr = pcm[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]
                st = 0 if i == 0 else (2 if i == n - 1 else 1)
                await ws.send(json.dumps({
                    "common": {"app_id": creds["APPID"]},
                    "business": {"language": "zh_cn", "domain": "iat", "accent": "mandarin", "vad_eos": 10000},
                    "data": {"status": st, "format": "audio/L16;rate=16000",
                             "encoding": "raw", "audio": base64.b64encode(fr).decode()},
                }))
                if done.is_set():
                    break
                if i % BATCH_FRAMES == BATCH_FRAMES - 1:
                    await asyncio.sleep(BURST_SLEEP_S)
        except Exception:  # noqa: BLE001 — 服务端提前收流（超长块被截断）：已收文本照用
            pass
        try:
            await asyncio.wait_for(rt, timeout=COLLECT_TIMEOUT_S)
        except asyncio.TimeoutError:
            rt.cancel()
    return texts


def transcribe_chunk(creds: dict, pcm: bytes) -> str:
    """整块合成一句（兼容接口）。失败返回空串并只提示一次。"""
    if not pcm:
        return ""
    import sys

    if sys.platform == "win32":  # websockets 在 Windows 需 Selector 事件循环
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        return "".join(asyncio.run(transcribe_chunk_async(creds, pcm)))
    except Exception as exc:  # noqa: BLE001 — 云服务不可用时如实降级为空块
        if not WARNED["once"]:
            WARNED["once"] = True
            print(f"[xfyun-iat] 转写失败（后续同类错误不再刷屏）: {type(exc).__name__}: {str(exc)[:120]}")
        return ""


class XfyunASR:
    """与 transcribe.asr.ASR 同接口（transcribe_pcm16k），供 Session 作为后端切换。"""

    def __init__(self, cred_file: Path):
        self.with_embeddings = False  # 云转写无句级声纹：说话人字段退化为 spk?
        # IAT 不返回句级时间戳（只按语句顺序给消息）→ 管线不能开重叠窗口：
        # 重叠块里同一句话两种切法文本必然不一致，按"同时间戳同文本"的去重挡不住，
        # 会逐块重复上一句的尾巴（实网 40s 集成测试实测）。见 pipeline._flush。
        self.sentence_timestamps = False
        p = Path(cred_file)
        if p.is_dir():  # 允许传"讯飞文件夹"：内部认领语音听写凭证
            from .xfyun_cred import find_iat

            hit = find_iat(p)
            if hit is None:
                raise ValueError(f"讯飞文件夹里没找到语音听写凭证（需含 APPID/APIKey/APISecret 的 .env）: {p}")
            p = hit
        self.creds = load_creds(p)

    def transcribe_pcm16k(self, pcm: bytes) -> list[dict]:
        if len(pcm) % 2:
            raise ValueError("16-bit PCM 必须包含完整采样点")
        import sys

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        try:
            texts = [t for t in asyncio.run(transcribe_chunk_async(self.creds, pcm)) if t.strip()]
        except Exception as exc:  # noqa: BLE001 — 云不可用降级为空块（只提示一次）
            if not WARNED["once"]:
                WARNED["once"] = True
                print(f"[xfyun-iat] 转写失败（后续同类错误不再刷屏）: {type(exc).__name__}: {str(exc)[:120]}")
            return []
        if not texts:
            return []
        # 无句级时间戳 → 按字数比例把块时长分摊给各语段（近似：显示/证据时间戳
        # 误差被块长 8s 封顶；证据片段本身是 300s 回溯窗，秒级误差不伤使用）。
        dur = len(pcm) / 2 / 16000
        weights = [max(len(t.strip()), 1) for t in texts]
        total = sum(weights)
        segs: list[dict] = []
        pos = 0.0
        for t, w in zip(texts, weights):
            share = dur * w / total
            segs.append({
                "start": round(pos, 2),
                "end": round(pos + share, 2),
                "speaker": "spk?",
                "text": t,
                "emb": None,
            })
            pos += share
        return segs