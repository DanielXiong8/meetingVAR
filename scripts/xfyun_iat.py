"""讯飞语音听写（流式版）长音频会话链转写。

文档约束（OCR 自官方 PDF）：wss://iat-api.xfyun.cn/v2/iat，单会话 ≤60s
（超 10s 无数据服务端主动断开），16k raw PCM，40ms/1280B 一帧，zh_cn 支持简单英文。

实测行为：服务端在自然句尾（静音判停，vad_eos）返回 status=2/ls=True 并关闭连接——
长音频需"会话链"：断点续传 + 1 秒重叠，合并时做边缘去重。

用法：
  python xfyun_iat.py --wav <16k mono wav> --out <txt> [--start 0] [--dur 600]
凭证从用户 .env 读取，不落盘、不打印。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import wave
from email.utils import formatdate
from pathlib import Path

CRED_FILE = Path(os.environ.get("XFYUN_CRED_FILE",
    r"D:\项目\联想未来办公AI创新挑战\讯飞\语音听写\Websocket服务接口认证信息.env"))
HOST = "iat-api.xfyun.cn"
PATH = "/v2/iat"
FRAME_BYTES = 1280          # 40ms @16k mono s16
FRAME_MS = 40
SESSION_MAX_S = 55          # 单会话留余量（上限 60s）
RESUME_OVERLAP_S = 1.0      # 断点续传重叠，防切词
VAD_EOS = 10000             # 静音判停上限（毫秒）


def load_creds() -> dict:
    creds = {}
    for line in CRED_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "#")):
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k in {"APPID", "APIKey", "APISecret"} and v:
            creds[k] = v
    missing = {"APPID", "APIKey", "APISecret"} - set(creds)
    if missing:
        raise SystemExit(f"凭证缺失: {missing}")
    return creds


def ws_url(creds: dict) -> str:
    date = formatdate(timeval=None, localtime=False, usegmt=True)
    origin = f"host: {HOST}\ndate: {date}\nGET {PATH} HTTP/1.1"
    sig = base64.b64encode(hmac.new(creds["APISecret"].encode(), origin.encode(), hashlib.sha256).digest()).decode()
    auth_origin = (
        f'api_key="{creds["APIKey"]}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{sig}"'
    )
    auth = base64.b64encode(auth_origin.encode()).decode()
    q = urllib.parse.urlencode({"authorization": auth, "date": date, "host": HOST})
    return f"wss://{HOST}{PATH}?{q}"


def _extract(msg: str) -> tuple[str, bool]:
    """返回 (本片文本, 是否会话结束)。"""
    try:
        d = json.loads(msg)
    except ValueError:
        return "", False
    data = d.get("data") or {}
    r = data.get("result") or {}
    txt = "".join(cw.get("w", "") for w in (r.get("ws") or []) for cw in (w.get("cw") or []))
    done = data.get("status") == 2 or bool(r.get("ls"))
    return txt, done


def _ws_connect(url: str, **kw):
    """websockets 13 的 connect 不收 proxy 参数（且 legacy 不读系统代理）；
    14+ 新实现默认从环境变量取代理，需要显式 proxy=None 才直连。"""
    import inspect

    import websockets

    if "proxy" in inspect.signature(websockets.connect).parameters:
        kw["proxy"] = None
    return websockets.connect(url, **kw)


async def transcribe_long(creds: dict, pcm: bytes, log_prefix: str = "") -> list[tuple[float, str]]:
    """会话链转写：返回 [(起始秒, 该会话文本)]，按时间顺序。"""
    n_frames = (len(pcm) + FRAME_BYTES - 1) // FRAME_BYTES
    out: list[tuple[float, str]] = []
    pos = 0
    session_no = 0
    fails = 0
    t_start = time.time()
    while pos < n_frames:
        session_no += 1
        texts: list[str] = []
        done = asyncio.Event()
        end_frame = min(pos + int(SESSION_MAX_S * 1000 / FRAME_MS), n_frames)

        try:
            ws_ctx = _ws_connect(ws_url(creds), max_size=2**22, open_timeout=10)
            async with ws_ctx as ws:
                print(f"    {log_prefix}会话{session_no} 已连接 @音频{pos*FRAME_MS/1000:.0f}s", flush=True)
                async def receiver() -> None:
                    try:
                        async for m in ws:
                            t, d = _extract(m)
                            if t:
                                texts.append(t)
                            if d:
                                done.set()
                    except Exception:  # noqa: BLE001
                        pass
                    finally:
                        done.set()

                rt = asyncio.create_task(receiver())
                i = pos
                sent_ok = pos
                while i < end_frame:
                    fr = pcm[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]
                    # 每个新连接的首帧必须是 status=0（会话链续传同理），末帧 2，其余 1
                    st = 0 if i == pos else (2 if i == end_frame - 1 else 1)
                    try:
                        await ws.send(json.dumps({
                            "common": {"app_id": creds["APPID"]},
                            "business": {"language": "zh_cn", "domain": "iat",
                                         "accent": "mandarin", "vad_eos": VAD_EOS},
                            "data": {"status": st, "format": "audio/L16;rate=16000",
                                     "encoding": "raw", "audio": base64.b64encode(fr).decode()},
                        }))
                        sent_ok = i + 1
                    except Exception as e:  # noqa: BLE001 — 服务端在句尾关闭
                        print(f"    {log_prefix}会话{session_no} 发送中断@帧{i}: {type(e).__name__}: {str(e)[:120]}", flush=True)
                        break
                    if done.is_set():
                        break
                    i += 1
                    await asyncio.sleep(FRAME_MS / 1000)
                try:
                    await asyncio.wait_for(rt, timeout=15)
                except asyncio.TimeoutError:
                    rt.cancel()
        except Exception as e:  # noqa: BLE001
            print(f"  {log_prefix}会话 {session_no} 异常: {type(e).__name__} {e}")

        text = "".join(texts)
        if text:
            out.append((pos * FRAME_MS / 1000, text))
        print(f"    {log_prefix}会话{session_no}: 音频 {pos*FRAME_MS/1000:.0f}s→{sent_ok*FRAME_MS/1000:.0f}s，"
              f"{len(text)} 字，累计 {time.time()-t_start:.0f}s", flush=True)
        if sent_ok >= n_frames:
            break
        if sent_ok - pos <= 25:  # 会话几乎没发送成功（并发/额度被拒）→ 退避后跳过 2s
            fails += 1
            await asyncio.sleep(min(5 * fails, 20))
            pos = min(n_frames, pos + int(2 * 1000 / FRAME_MS))
            continue
        fails = 0
        await asyncio.sleep(4)  # 会话间冷却：友好对待服务端并发限制
        # 断点续传：回退 1s 重叠防切词；至少要前进 1 帧
        back = int(RESUME_OVERLAP_S * 1000 / FRAME_MS)
        pos = max(pos + 1, sent_ok - back)
    return out


def read_wav_16k(path: Path, start_s: float, dur_s: float | None) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "需要 16k mono s16 wav"
        total = w.getnframes()
        w.setpos(int(start_s * 16000))
        n = total - int(start_s * 16000) if dur_s is None else int(dur_s * 16000)
        return w.readframes(n)


def merge_texts(items: list[tuple[float, str]], overlap_chars: int = 12) -> str:
    """合并会话链文本：相邻会话边缘去重（重叠 ≤ overlap_chars 的公共子串）。"""
    merged = ""
    for _, t in items:
        if not merged:
            merged = t
            continue
        k = min(overlap_chars, len(merged), len(t))
        cut = 0
        for j in range(k, 3, -1):
            if merged[-j:] == t[:j]:
                cut = j
                break
        merged += t[cut:]
    return merged


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=None)
    a = ap.parse_args()

    creds = load_creds()
    print(f"[xfyun] 凭证就绪（APPID 尾号 {creds['APPID'][-4:]}）")
    pcm = read_wav_16k(Path(a.wav), a.start, a.dur)
    dur = len(pcm) / 32000
    print(f"[xfyun] 音频 {dur:.0f}s，会话链转写（实时流式，预计耗时≈音频时长）")

    t0 = time.time()
    items = await transcribe_long(creds, pcm, log_prefix="")
    lines = []
    for off, t in items:
        h, rem = divmod(int(a.start + off), 3600)
        m, s = divmod(rem, 60)
        lines.append(f"[{h:02d}:{m:02d}:{s:02d}] spk0: {t}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[xfyun] 完成 -> {out}（{len(lines)} 句，{sum(len(t) for _,t in items)} 字，"
          f"总用时 {time.time()-t0:.0f}s）")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
