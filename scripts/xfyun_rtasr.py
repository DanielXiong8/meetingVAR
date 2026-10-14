"""讯飞实时语音转写（RTASR 标准版）长音频转写。

文档约束（OCR 自官方 PDF）：wss://rtasr.xfyun.cn/v1/ws?appid&ts&signa[&lang=cn]，
signa = base64(HmacSHA1(MD5(appid+ts), api_key))。实测（2026-10-14）：本账号
MD5 必须取 hexdigest 字符串（与 LFASR 一致；官方老 demo 的 digest 字节写法报 10110
"no license|illegal signa"），脚本失败会自动回退 digest 变体重试一次。音频 16k/16bit/mono，
40ms/1280B 一帧（间隔超 15s 服务端断开），发完补 {"end": true} 文本帧收尾。

服务端消息：{"action":"started"|"result"|"error","code","desc","data","sid"}，
result 的 data 是 JSON 字符串：cn.st.type "0"=中间结果 / "1"=分段最终结果，
st.rt[].ws[].cw[].w 逐字拼接；先 dump 原始消息（*.debug.jsonl）核对 schema。

用法：
  python xfyun_rtasr.py --wav <16k mono wav> --out <txt> [--start 0] [--dur 90] [--role]
凭证从 .env 读取，不落盘、不打印。
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
from pathlib import Path

CRED_FILE = Path(os.environ.get("XFYUN_RTASR_CRED_FILE",
    r"D:\项目\联想未来办公AI创新挑战\讯飞\实时语音转写\Websocket认证服务信息.env"))
HOST = "rtasr.xfyun.cn"
PATH = "/v1/ws"
FRAME_BYTES = 1280          # 40ms @16k mono s16
FRAME_MS = 40


def load_creds() -> dict:
    creds = {}
    for line in CRED_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "#")):
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k in {"APPID", "APIKey"} and v:
            creds[k] = v
    missing = {"APPID", "APIKey"} - set(creds)
    if missing:
        raise SystemExit(f"凭证缺失: {missing}")
    return creds


def make_signa(appid: str, api_key: str, ts: str, md5_mode: str) -> str:
    m = hashlib.md5(f"{appid}{ts}".encode())
    msg = m.digest() if md5_mode == "digest" else m.hexdigest().encode()
    sig = hmac.new(api_key.encode(), msg, hashlib.sha1).digest()
    return base64.b64encode(sig).decode()


def ws_url(creds: dict, md5_mode: str, role: bool) -> str:
    ts = str(int(time.time()))
    q = {
        "appid": creds["APPID"],
        "ts": ts,
        "signa": make_signa(creds["APPID"], creds["APIKey"], ts, md5_mode),
        "lang": "cn",
    }
    if role:
        q["roleType"] = "2"
    return f"wss://{HOST}{PATH}?" + urllib.parse.urlencode(q)


def parse_result_data(data_str: str) -> list[dict]:
    """把 result 消息的 data 解析为分段列表：{type, text, seg_id, bg, ed, ls, role}。

    实测（2026-10-14）：type="1" 是当前 VAD 句的逐词生长快照（多条、会重叠、只用于实时显示）；
    type="0" 才是句子的最终结果（带标点、bg/ed 覆盖整句），按到达顺序追加即得全文。
    最后一条 ls=true 表示服务端收尾。
    roleType=2 时 cw.rl 是"说话人切换标记"：只在切换点标非零值，其余字符为 0
    （文档："该值只有角色切换才变化，其余时刻值为0"），需状态机重组——rl_seq 保留原序列。"""
    try:
        d = json.loads(data_str)
    except ValueError:
        return []
    root = d.get("cn") or d.get("en") or {}
    st = root.get("st") or {}
    text = ""
    rl_seq: list[str] = []
    for rt in st.get("rt") or []:
        for ws_ in rt.get("ws") or []:
            for cw in ws_.get("cw") or []:
                text += cw.get("w", "")
                rl_seq.append(str(cw.get("rl") or "0"))
    return [{
        "type": str(st.get("type", "")),
        "text": text,
        "seg_id": d.get("seg_id"),
        "bg": st.get("bg"),
        "ed": st.get("ed"),
        "ls": bool(d.get("ls")),
        "rl_seq": rl_seq,
    }]


def _ws_connect(url: str, **kw):
    """websockets 13 的 connect 不收 proxy 参数（且 legacy 不读系统代理）；
    14+ 新实现默认从环境变量取代理，需要显式 proxy=None 才直连。"""
    import inspect

    import websockets

    if "proxy" in inspect.signature(websockets.connect).parameters:
        kw["proxy"] = None
    return websockets.connect(url, **kw)


async def run(creds: dict, pcm: bytes, role: bool, start_s: float, dbg: Path) -> None:
    n_frames = (len(pcm) + FRAME_BYTES - 1) // FRAME_BYTES
    md5_mode = "hexdigest"  # 实测（2026-10-14）：本账号 hexdigest 变体通过；digest 报 10110
    raw_lines: list[dict] = []
    auth_error: dict | None = None

    for attempt in (1, 2):
        raw_lines.clear()
        auth_error = None
        print(f"[rtasr] 连接（md5={md5_mode}, role={role}, attempt={attempt}）…", flush=True)
        url = ws_url(creds, md5_mode, role)
        started = asyncio.Event()
        closed = asyncio.Event()
        sent_frames = 0

        async def receiver(ws) -> None:
            nonlocal auth_error
            try:
                async for m in ws:
                    if isinstance(m, bytes):
                        m = m.decode("utf-8", "replace")
                    try:
                        msg = json.loads(m)
                    except ValueError:
                        continue
                    raw_lines.append(msg)
                    act = msg.get("action")
                    if act == "started":
                        started.set()
                    elif act == "error":
                        auth_error = msg
                        started.set()
                        closed.set()
            except Exception:  # noqa: BLE001
                pass
            finally:
                closed.set()

        try:
            async with _ws_connect(url, max_size=2**24, open_timeout=10) as ws:
                rt = asyncio.create_task(receiver(ws))
                try:
                    await asyncio.wait_for(started.wait(), timeout=8)
                except asyncio.TimeoutError:
                    print("[rtasr] 8s 内未收到 started，仍继续推流", flush=True)
                if auth_error:
                    code = auth_error.get("code")
                    desc = str(auth_error.get("desc"))[:80]
                    print(f"[rtasr] 服务端拒绝: code={code} desc={desc}", flush=True)
                    # 10105/10110 的 desc 对"签名非法/无授权"是双义的，先换 MD5 变体重试一次
                    if attempt == 1:
                        md5_mode = "digest"
                        rt.cancel()
                        continue
                    raise SystemExit(f"鉴权失败且回退无效: {code} {desc}")
                t0 = time.time()
                i = 0
                while i < n_frames:
                    fr = pcm[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]
                    try:
                        await ws.send(fr)
                        sent_frames = i + 1
                    except Exception as e:  # noqa: BLE001
                        print(f"[rtasr] 发送中断@帧{i}: {type(e).__name__}: {str(e)[:100]}", flush=True)
                        break
                    i += 1
                    await asyncio.sleep(FRAME_MS / 1000)
                print(f"[rtasr] 音频推完 {sent_frames * FRAME_MS / 1000:.1f}s，用时 {time.time()-t0:.0f}s，发送 end 帧", flush=True)
                try:
                    await ws.send(json.dumps({"end": True}))
                except Exception as e:  # noqa: BLE001
                    print(f"[rtasr] end 帧发送失败: {e}", flush=True)
                try:
                    await asyncio.wait_for(closed.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
                rt.cancel()
            # 连接正常走完 → 不再重试
            break
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[rtasr] 连接异常: {type(e).__name__}: {str(e)[:120]}", flush=True)
            if attempt == 2:
                raise

    dbg.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in raw_lines) + "\n",
        encoding="utf-8",
    )


def read_wav_16k(path: Path, start_s: float, dur_s: float | None) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "需要 16k mono s16 wav"
        total = w.getnframes()
        w.setpos(int(start_s * 16000))
        n = total - int(start_s * 16000) if dur_s is None else int(dur_s * 16000)
        return w.readframes(n)


def assemble_finals(raw_lines: list[dict]) -> list[dict]:
    """从原始消息序列重组最终文本（type=0），连续重复去重；角色用状态机重组。"""
    finals: list[dict] = []
    cur_role = "0"
    for msg in raw_lines:
        if msg.get("action") != "result":
            continue
        for s in parse_result_data(msg.get("data") or ""):
            if s["type"] != "0":
                continue
            markers = [rl for rl in s["rl_seq"] if rl not in (None, "0")]
            if markers:
                cur_role = markers[-1]
            if finals and finals[-1]["text"] == s["text"]:
                continue
            finals.append({
                "audio_pos_s": int(s["bg"] or 0) / 1000,
                "seg_id": s["seg_id"],
                "role": cur_role,
                "text": s["text"],
            })
    return finals


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=None)
    ap.add_argument("--role", action="store_true", help="开启角色分离 roleType=2")
    ap.add_argument("--from-debug", default=None, help="离线模式：从已有 .debug.jsonl 重组，不再调 API")
    a = ap.parse_args()

    out = Path(a.out)
    dbg = out.with_suffix(".debug.jsonl")
    t0 = time.time()
    if a.from_debug:
        raw = [json.loads(x) for x in Path(a.from_debug).read_text(encoding="utf-8").splitlines() if x.strip()]
        finals = assemble_finals(raw)
        print(f"[rtasr] 离线重组 {len(raw)} 条消息 -> {len(finals)} 句")
    else:
        creds = load_creds()
        print(f"[rtasr] 凭证就绪（APPID {creds['APPID']}）")
        pcm = read_wav_16k(Path(a.wav), a.start, a.dur)
        print(f"[rtasr] 音频 {len(pcm) / 32000:.0f}s，实时节奏推流（预计耗时≈音频时长）")
        await run(creds, pcm, a.role, a.start, dbg)
        raw = [json.loads(x) for x in dbg.read_text(encoding="utf-8").splitlines() if x.strip()]
        finals = assemble_finals(raw)

    lines = []
    for f in finals:
        total = int(a.start + f["audio_pos_s"])
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        role = f.get("role") or "0"
        lines.append(f"[{h:02d}:{m:02d}:{s:02d}] spk{role}: {f['text']}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    total_chars = sum(len(f["text"]) for f in finals)
    print(f"[rtasr] 完成 -> {out}（{len(finals)} 段，{total_chars} 字，总用时 {time.time()-t0:.0f}s）")
    print(f"[rtasr] 原始消息 -> {dbg}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
