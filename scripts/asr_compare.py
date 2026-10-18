"""ASR 引擎对比 harness（C 路线第二步：让"讯飞好在哪"变成数字）。

用法：
  1) 本地引擎（无需凭证）：
     python scripts/asr_compare.py --wav <样段> --local
  2) 讯飞侧：先在讯飞开放平台开通「长语音转写」，把 APPID/API_KEY/API_SECRET
     设为环境变量 XFYUN_APP_ID / XFYUN_API_KEY / XFYUN_API_SECRET，再跑 --xfyun。
  3) 有人工核对过的参考转写时加 --reference <txt>，输出 CER；
     没有则输出并排对比文本（定性目检）。

产物：asr_compare/out/<引擎>.txt（与 meetingvar transcribe 相同的行格式）。
注意：讯飞侧会把音频上传出域——这是用户明示授权的评测动作，不违反默认零落盘。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LINE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s+(\S+):\s*(.*)$")


def strip_ts(text: str) -> str:
    """只留纯文本行（CER 计算/并排目检用）。"""
    out = []
    for l in text.splitlines():
        m = LINE.match(l.strip())
        out.append(m.group(3).strip() if m else l.strip())
    return "\n".join(x for x in out if x)


def run_local(wav: Path, out: Path, minutes: float) -> None:
    """SenseVoice 本地引擎：直接复用 meetingvar transcribe（无词表 = 无纠错，公平对比）。"""
    from meetingvar.replay import replay

    replay(wav, f"asrcmp_{int(minutes)}m", terms=[], focus_docs=None, device="cuda", transcript_out=out)


def run_xfyun(wav: Path, out: Path) -> None:
    """讯飞长语音转写（需要 XFYUN_APP_ID/XFYUN_API_KEY/XFYUN_API_SECRET）。

    流程：计算签名 → 上传音频 → 轮询任务 → 下载订单结果，拼成行格式。
    实现按讯飞官方文档（https://www.xfyun.cn/doc/asr/lfasr/API.html）；
    首次运行请先用 1 分钟音频验证凭证，再上 10 分钟样段。
    """
    import base64
    import hashlib
    import hmac
    import json
    import time
    import urllib.parse
    import urllib.request

    app_id = os.environ.get("XFYUN_APP_ID", "")
    api_key = os.environ.get("XFYUN_API_KEY", "")
    api_secret = os.environ.get("XFYUN_API_SECRET", "")
    if not (app_id and api_key and api_secret):
        raise SystemExit("[xfyun] 缺凭证：请设置 XFYUN_APP_ID / XFYUN_API_KEY / XFYUN_API_SECRET")

    base = "https://raasr.xfyun.cn/v2/api"

    def sign(ts: str) -> str:
        # 官方签名：HmacSHA256(base_string, api_secret) → base64
        base_string = app_id + ts
        return base64.b64encode(
            hmac.new(api_secret.encode(), base_string.encode(), hashlib.sha256).digest()
        ).decode()

    ts = str(int(time.time()))
    sig = sign(ts)
    file_size = wav.stat().st_size
    params = {
        "appId": app_id, "signa": sig, "ts": ts,
        "fileLen": str(file_size), "fileName": wav.name, "fileMd5": _md5(wav),
    }
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{base}/upload", data=body)
    with urllib.request.urlopen(req, timeout=60) as r:
        up = json.loads(r.read())
    if str(up.get("code")) != "0":
        raise SystemExit(f"[xfyun] 上传失败: {up}")
    order_id = up["content"]["orderId"]
    print(f"[xfyun] 上传完成 orderId={order_id}，轮询转写…")

    while True:
        time.sleep(10)
        q = urllib.parse.urlencode({"appId": app_id, "signa": sign(str(int(time.time()))), "orderId": order_id}).encode()
        with urllib.request.urlopen(urllib.request.Request(f"{base}/getResult", data=q), timeout=60) as r:
            res = json.loads(r.read())
        st = res.get("content", {}).get("orderInfo", {}).get("status")
        if st == 4:  # 4 = 转写完成
            break
        if st == -1:
            raise SystemExit(f"[xfyun] 转写失败: {res}")
        print(f"[xfyun] status={st} …")
        if time.time() % 300 < 10:  # 约 5 分钟提醒一次
            pass

    detail = json.loads(res["content"]["orderResult"])
    lines = []
    for seg in detail.get("detail", []):
        for w in seg.get("words", []):  # 官方返回按词/句碎片，需按段拼装
            pass
    # orderResult.detail 每段含 bg/ed（毫秒）与 onebest（该段文本）
    for seg in detail.get("detail", []):
        t_ms = int(seg.get("bg", 0))
        h, rem = divmod(t_ms // 1000, 3600)
        m, s = divmod(rem, 60)
        lines.append(f"[{h:02d}:{m:02d}:{s:02d}] spk0: {seg.get('onebest', '').strip()}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[xfyun] 完成 -> {out}（{len(lines)} 句）")


def _md5(p: Path) -> str:
    import hashlib

    return hashlib.md5(p.read_bytes()).hexdigest()


def cer(ref: str, hyp: str) -> float:
    """字符错误率（中文为主；英文按整串近似，仅作相对比较）。"""
    import difflib

    a, b = list(ref.replace("\n", "")), list(hyp.replace("\n", ""))
    sm = difflib.SequenceMatcher(None, a, b)
    match = sum(m.size for m in sm.get_matching_blocks())
    return 1.0 - match / max(len(a), 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--local", action="store_true", help="跑 SenseVoice 本地引擎")
    ap.add_argument("--xfyun", action="store_true", help="跑讯飞长语音（需凭证环境变量）")
    ap.add_argument("--reference", default=None, help="人工核对过的参考转写 txt（提供则输出 CER）")
    ap.add_argument("--out", default=None, help="产物目录（默认 <wav 所在目录>/out）")
    a = ap.parse_args()

    wav = Path(a.wav)
    out_dir = Path(a.out) if a.out else wav.parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.local:
        out = out_dir / "sensevoice.txt"
        print(f"[local] 转写中（无词表、无纠错）-> {out}")
        run_local(wav, out, 10)
    if a.xfyun:
        run_xfyun(wav, out_dir / "xfyun.txt")

    files = {p.stem: strip_ts(p.read_text(encoding="utf-8")) for p in out_dir.glob("*.txt") if p.name != "reference.txt"}
    if a.reference:
        ref = strip_ts(Path(a.reference).read_text(encoding="utf-8"))
        print(f"\n=== CER vs 参考（{len(ref)} 字符） ===")
        for name, hyp in files.items():
            print(f"  {name}: {cer(ref, hyp) * 100:.1f}%")
    print("\n=== 并排对比（前 20 行） ===")
    cols = {n: [l for l in t.splitlines() if l.strip()] for n, t in files.items()}
    n = max(len(v) for v in cols.values())
    for i in range(min(n, 20)):
        print("  |  ".join(f"{col[:38]}".ljust(40) for col in (cols[k][i] if i < len(cols[k]) else "" for k in cols)))


if __name__ == "__main__":
    main()
