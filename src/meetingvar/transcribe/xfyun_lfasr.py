"""讯飞录音文件转写（标准版）客户端：upload → 轮询 getResult → 提取带时间戳的行。

用途：UI"文件转写"工具（本地媒体文件 → 高质量离线转写稿）。
实测要点（2026-10-14，scripts/xfyun_lfasr.py 溯源）：
- signa = base64(HmacSHA1(MD5hex(appid+ts), secretKey))（hexdigest 写法）；
- status/failType 在 content.orderInfo；orderResult 里每个 lattice item 一句；
- st.bg/ed 单位 ms（绝对），ws.wb/we 单位 10ms（相对 bg）；
- roleType=1 时 st.rl 为角色号（说话人分离）；
- 凭证文件（APPID/SecretKey）单独给出，不打印。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://raasr.xfyun.cn/v2/api"
POLL_INTERVAL_S = 12
POLL_BUDGET_S = 1800  # 5 分钟音参考 ≤10 分钟出结果；给宽裕预算
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连（绕系统代理）


def load_creds(path: Path) -> dict:
    p = Path(path)
    if p.is_dir():  # 允许传"讯飞文件夹"：内部认领录音文件转写凭证
        from .xfyun_cred import find_lfasr

        hit = find_lfasr(p)
        if hit is None:
            raise ValueError(f"讯飞文件夹里没找到录音文件转写凭证（需含 APPID/SecretKey 的 .env）: {p}")
        p = hit
    creds = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "#")):
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k in {"APPID", "SecretKey"} and v:
            creds[k] = v
    missing = {"APPID", "SecretKey"} - set(creds)
    if missing:
        raise ValueError(f"录音文件转写凭证缺失: {sorted(missing)}（文件 {path}）")
    return creds


def make_signa(appid: str, secret: str, ts: str, md5_mode: str = "hexdigest") -> str:
    m = hashlib.md5(f"{appid}{ts}".encode())
    msg = m.hexdigest().encode() if md5_mode == "hexdigest" else m.digest()
    sig = hmac.new(secret.encode(), msg, hashlib.sha1).digest()
    return base64.b64encode(sig).decode()


def _post(url: str, params: dict, body: bytes | None = None, content_type: str | None = None) -> dict:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", data=body or b"", method="POST")
    if content_type:
        req.add_header("Content-Type", content_type)
    with _OPENER.open(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def upload(creds: dict, wav: Path, duration_ms: int, role: bool = True, hotword: str | None = None) -> str:
    ts = str(int(time.time()))
    params = {
        "appId": creds["APPID"],
        "signa": make_signa(creds["APPID"], creds["SecretKey"], ts),
        "ts": ts,
        "fileName": Path(wav).name,
        "fileSize": str(Path(wav).stat().st_size),
        "duration": str(int(duration_ms)),
        "language": "cn",
    }
    if role:
        params["roleType"] = "1"
    if hotword:
        params["hotWord"] = hotword
    resp = _post(f"{BASE}/upload", params, Path(wav).read_bytes(), "application/octet-stream")
    if str(resp.get("code")) not in {"000000", "0"}:
        raise RuntimeError(f"upload 失败: {resp.get('code')} {resp.get('descInfo')}")
    content = resp.get("content") or {}
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = {}
    order_id = resp.get("orderId") or content.get("orderId")
    if not order_id:
        raise RuntimeError(f"upload 成功但无 orderId: {resp}")
    return str(order_id)


def poll(creds: dict, order_id: str, progress=None) -> dict:
    t0 = time.time()
    while time.time() - t0 < POLL_BUDGET_S:
        ts = str(int(time.time()))
        params = {
            "appId": creds["APPID"],
            "signa": make_signa(creds["APPID"], creds["SecretKey"], ts),
            "ts": ts,
            "orderId": order_id,
            "resultType": "transfer",
        }
        resp = _post(f"{BASE}/getResult", params)
        content = resp.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                content = {}
        if not isinstance(content, dict):
            content = {}
        info = content.get("orderInfo") or {}
        status = str(info.get("status", ""))
        fail = str(info.get("failType", "0"))
        if progress:
            progress(f"轮询 {time.time()-t0:.0f}s: status={status} failType={fail}")
        if str(resp.get("code")) != "000000":
            raise RuntimeError(f"getResult 失败: {resp.get('code')} {resp.get('descInfo')}")
        if fail not in {"0", "None", ""}:
            raise RuntimeError(f"转写任务失败 failType={fail}")
        if status == "4":
            return {**resp, **content}
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"轮询超时（{POLL_BUDGET_S}s）orderId={order_id}")


def extract_lines(merged: dict, start_s: float = 0.0) -> list[tuple[float, str, str]]:
    """返回 [(秒, 角色号或'0', 文本)]；单位换算见模块头实测要点。"""
    lattice = merged.get("lattice")
    if lattice is None:
        raw = merged.get("orderResult") or "{}"
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
        lattice = raw.get("lattice") or []
    lines: list[tuple[float, str, str]] = []
    for item in lattice:
        try:
            best = json.loads(item.get("json_1best") or "{}")
        except ValueError:
            continue
        st = best.get("st") or {}
        bg = int(st.get("bg") or 0)
        role = str(st.get("rl") or "0")
        text = "".join(
            cw.get("w", "")
            for rt in st.get("rt") or []
            for ws_ in rt.get("ws") or []
            for cw in ws_.get("cw") or []
        )
        wb_ticks = None
        for rt in st.get("rt") or []:
            for ws_ in rt.get("ws") or []:
                if ws_.get("wb") is not None:
                    wb_ticks = int(ws_["wb"])
                    break
            if wb_ticks is not None:
                break
        pos_ms = bg + (wb_ticks * 10 if wb_ticks is not None else 0)
        lines.append((start_s + pos_ms / 1000.0, role, text))
    return lines


def transcribe_file(wav: Path, cred_file: Path, duration_s: float | None = None,
                    progress=None) -> dict:
    """完整流程：上传→轮询→返回 {order_id, lines, text}。"""
    import wave

    wav = Path(wav)
    if duration_s is None:
        with wave.open(str(wav), "rb") as w:
            duration_s = w.getnframes() / w.getframerate()
    creds = load_creds(cred_file)
    if progress:
        progress(f"上传 {wav.name}（{duration_s/60:.1f} 分钟）…")
    order_id = upload(creds, wav, int(duration_s * 1000), role=True)
    if progress:
        progress(f"orderId={order_id}，等待转写…")
    merged = poll(creds, order_id, progress=progress)
    lines = extract_lines(merged)
    return {"order_id": order_id, "lines": lines, "text": "\n".join(t for _, _, t in lines)}


def fmt_lines(lines: list[tuple[float, str, str]]) -> str:
    out = []
    for sec, role, text in lines:
        mm, ss = divmod(int(sec), 60)
        spk = f"spk{role}" if role != "0" else "spk0"
        out.append(f"[{mm:02d}:{ss:02d}] {spk}: {text}")
    return "\n".join(out)