"""讯飞录音文件转写（LFASR 标准版）离线转写。

文档约束（OCR 自官方 PDF）：
- 上传：POST https://raasr.xfyun.cn/v2/api/upload?appId&signa&ts&fileName&fileSize&duration&language[&roleType=1]
  body = 音频字节（Content-Type: application/octet-stream），返回 orderId。
- 查询：POST https://raasr.xfyun.cn/v2/api/getResult?appId&signa&ts&orderId&resultType=transfer
  轮询至 status=4（转写完成）/ failType≠0（失败）。
- signa = base64(HmacSHA1(MD5hex(appid+ts), secret_key))（官方 demo 写法）；
  若 26601 desk verify fail 自动回退 digest 变体。
- 支持 wav/flac/mp3/opus/m4a/aac/ogg/amr…（≤500MB、≤5 小时）；roleType=1 通用角色分离。

用法：
  python xfyun_lfasr.py --wav <16k mono wav> --out <txt> [--role] [--hotword "词1|词2"]
凭证从 .env 读取，不落盘、不打印。热词分隔符是竖线 `|`（官方文档），
不是空格；单次最多 200 个，每个 2–16 字。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

CRED_FILE = Path(os.environ.get("XFYUN_LFASR_CRED_FILE",
    r"D:\项目\联想未来办公AI创新挑战\讯飞\录音文件转写（标准版）\Websocket认证信息.env"))
BASE = "https://raasr.xfyun.cn/v2/api"
POLL_INTERVAL_S = 15
POLL_BUDGET_S = 600  # 官方 SLA：60min 内音频 ≤20min 出结果；90s 音频给 10min 预算

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕开系统代理直连


def load_creds() -> dict:
    creds = {}
    for line in CRED_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "#")):
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k in {"APPID", "SecretKey"} and v:
            creds[k] = v
    missing = {"APPID", "SecretKey"} - set(creds)
    if missing:
        raise SystemExit(f"凭证缺失: {missing}")
    return creds


def make_signa(appid: str, secret: str, ts: str, md5_mode: str) -> str:
    m = hashlib.md5(f"{appid}{ts}".encode())
    msg = m.hexdigest().encode() if md5_mode == "hexdigest" else m.digest()
    sig = hmac.new(secret.encode(), msg, hashlib.sha1).digest()
    return base64.b64encode(sig).decode()


def http_post(url: str, params: dict, body: bytes | None = None, content_type: str | None = None) -> dict:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", data=body or b"", method="POST")
    if content_type:
        req.add_header("Content-Type", content_type)
    with _OPENER.open(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def upload(creds: dict, wav: Path, duration_ms: int, role: bool, hotword: str | None, md5_mode: str) -> str:
    ts = str(int(time.time()))
    params = {
        "appId": creds["APPID"],
        "signa": make_signa(creds["APPID"], creds["SecretKey"], ts, md5_mode),
        "ts": ts,
        "fileName": wav.name,
        "fileSize": str(wav.stat().st_size),
        "duration": str(duration_ms),
        "language": "cn",
    }
    if role:
        params["roleType"] = "1"
    if hotword:
        params["hotWord"] = hotword
    resp = http_post(f"{BASE}/upload", params, wav.read_bytes(), "application/octet-stream")
    print(f"[lfasr] upload 响应: code={resp.get('code')} desc={str(resp.get('descInfo'))[:60]}")
    if str(resp.get("code")) not in {"000000", "0"}:
        raise SystemExit(f"upload 失败: {resp}")
    content = resp.get("content") or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = {}
    order_id = resp.get("orderId") or content.get("orderId")
    if not order_id:
        raise SystemExit(f"upload 成功但未拿到 orderId: {resp}")
    return str(order_id)


def poll(creds: dict, order_id: str, md5_mode: str, dbg: Path) -> dict:
    """实测（2026-10-14）：status/failType 在 content.orderInfo 里；orderResult 也在 content。
    完成时 status=4 且 failType=0（原始耗时原样返回，毫秒）。"""
    t0 = time.time()
    last = {}
    while time.time() - t0 < POLL_BUDGET_S:
        ts = str(int(time.time()))
        params = {
            "appId": creds["APPID"],
            "signa": make_signa(creds["APPID"], creds["SecretKey"], ts, md5_mode),
            "ts": ts,
            "orderId": order_id,
            "resultType": "transfer",
        }
        resp = http_post(f"{BASE}/getResult", params)
        dbg.write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
        code = str(resp.get("code"))
        content = resp.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                content = {}
        if not isinstance(content, dict):
            content = {}
        info = content.get("orderInfo") or {}
        merged = {**resp, **content}
        status = str(info.get("status", ""))
        fail = str(info.get("failType", "0"))
        last = merged
        print(f"[lfasr] 轮询 {time.time()-t0:.0f}s: code={code} status={status} failType={fail} "
              f"originalDuration={info.get('originalDuration')} realDuration={info.get('realDuration')}", flush=True)
        if code != "000000":
            raise SystemExit(f"getResult 失败: {resp}")
        if fail not in {"0", "None", ""}:
            raise SystemExit(f"转写任务失败 failType={fail}: {str(merged.get('descInfo'))[:80]}")
        if status == "4":
            return merged
        time.sleep(POLL_INTERVAL_S)
    raise SystemExit(f"轮询超时（{POLL_BUDGET_S}s）: status={last.get('orderInfo')}")


def extract_lines(merged: dict, start_s: float) -> list[tuple[float, str, str]]:
    """从 orderResult 的 lattice 提取 (秒, 角色, 文本) 列表。"""
    lattice = merged.get("lattice")
    if lattice is None:
        order_result = merged.get("orderResult") or "{}"
        try:
            order_result = json.loads(order_result)
        except ValueError:
            order_result = {}
        lattice = order_result.get("lattice") or []
    lines: list[tuple[float, str, str]] = []
    for item in lattice:
        try:
            best = json.loads(item.get("json_1best") or "{}")
        except ValueError:
            continue
        st = best.get("st") or {}
        # 实测（2026-10-14）：每个 lattice item 一句；bg/rl/ed 在 st 层。
        # st.bg/ed 单位 ms（绝对）；ws.wb/we 单位 10ms；gw 相对 bg 偏移。
        bg = int(st.get("bg") or 0)
        role = str(st.get("rl") or "0")
        for rt in st.get("rt") or []:
            text = "".join(
                cw.get("w", "")
                for ws_ in (rt.get("ws") or [])
                for cw in (ws_.get("cw") or [])
            )
            wb_ticks = None
            for ws_ in (rt.get("ws") or []):
                if ws_.get("wb") is not None:
                    wb_ticks = int(ws_["wb"])
                    break
            pos_ms = bg + (wb_ticks * 10 if wb_ticks is not None else 0)
            lines.append((start_s + pos_ms / 1000.0, role, text))
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--role", action="store_true", help="开启角色分离 roleType=1")
    ap.add_argument("--hotword", default=None, help='热词，竖线分隔，如 "方向导数|偏导数"')
    ap.add_argument("--order-id", default=None, help="跳过上传，直接查询已有订单（72h 内有效）")
    a = ap.parse_args()

    creds = load_creds()
    print(f"[lfasr] 凭证就绪（APPID {creds['APPID']}）")
    out = Path(a.out)
    dbg = out.with_suffix(".debug.json")
    t0 = time.time()
    if a.order_id:
        order_id = a.order_id
        print(f"[lfasr] 直接查询已有订单 {order_id}（不重新上传）")
    else:
        wav = Path(a.wav)
        with wave.open(str(wav), "rb") as w:
            dur_s = w.getnframes() / w.getframerate()
        print(f"[lfasr] 音频 {dur_s:.1f}s，{wav.stat().st_size/1e6:.2f}MB，上传…")
        order_id = upload(creds, wav, int(dur_s * 1000), a.role, a.hotword, "hexdigest")
    print(f"[lfasr] orderId={order_id}，轮询结果（间隔 {POLL_INTERVAL_S}s）…")
    merged = poll(creds, order_id, "hexdigest", dbg)

    lines = extract_lines(merged, a.start)
    txt = []
    for sec, role, text in lines:
        total = int(sec)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        txt.append(f"[{h:02d}:{m:02d}:{s:02d}] spk{role}: {text}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(txt) + "\n", encoding="utf-8")
    total_chars = sum(len(t) for _, _, t in lines)
    print(f"[lfasr] 完成 -> {out}（{len(lines)} 句，{total_chars} 字，总用时 {time.time()-t0:.0f}s）")
    print(f"[lfasr] 原始结果 -> {dbg}")


if __name__ == "__main__":
    main()
