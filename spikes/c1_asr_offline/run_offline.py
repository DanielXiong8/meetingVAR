"""Spike C1 验收脚本：离线（断网）跑通 SenseVoice 转写 + CAM++ 说话人分离。

用法（模型权重已由 snapshot_download 固化到 D:/env/models）：
    python run_offline.py --wav 某段对话.wav [--device cpu]

验收标准：
  1. 拔网线后运行成功（脚本不访问任何网络，权重全部本地加载）
  2. 输出带说话人标签的分段转写
  3. 记录单句延迟（写入 SETUP.md）

若 AutoModel(spk_model=...) 组合失败，自动退化为纯转写（无说话人），
并在输出里明确标注 —— 这本身也是 SETUP.md 要记的坑。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

MODELS_ROOT = Path("D:/env/models")


def find_model_dir(keyword: str) -> str:
    """在 modelscope 缓存目录里按关键词定位模型目录（认 config.yaml 或 configuration.json）。"""
    hits = [
        p.parent
        for p in list(MODELS_ROOT.rglob("config.yaml")) + list(MODELS_ROOT.rglob("configuration.json"))
        if keyword.lower() in str(p).lower()
    ]
    if not hits:
        raise FileNotFoundError(f"MODELS_ROOT 下没找到含 {keyword!r} 的模型：检查 snapshot_download 是否完成")
    return str(hits[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True, help="输入 wav（16k 或任意采样率，funasr 自动重采样）")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    args = ap.parse_args()

    sv_dir = find_model_dir("SenseVoiceSmall")
    campp_dir = find_model_dir("campplus")
    vad_dir = find_model_dir("fsmn_vad")
    print(f"[load] SenseVoice: {sv_dir}\n[load] CAM++:     {campp_dir}\n[load] fsmn-vad:  {vad_dir}")

    from funasr import AutoModel
    import re

    def clean(t: str) -> str:
        return re.sub(r"<\|[^|]*\|>", "", t)

    try:
        model = AutoModel(
            model=sv_dir,
            vad_model=vad_dir,
            spk_model=campp_dir,
            device=args.device,
            disable_update=True,
            disable_pbar=True,
        )
        spk = True
    except Exception as e:  # noqa: BLE001 — 退化路径本身就是实验结果
        print(f"[warn] spk_model 组合失败（{e}），退化为纯转写")
        model = AutoModel(model=sv_dir, device=args.device, disable_update=True, disable_pbar=True)
        spk = False

    t0 = time.time()
    res = model.generate(input=args.wav, batch_size_s=60)
    dt = time.time() - t0

    print(f"\n[result] spk_model={'on' if spk else 'off'}  elapsed={dt:.1f}s")
    if spk and "sentence_info" in res[0]:
        for seg in res[0]["sentence_info"]:
            print(f"  [spk{seg.get('spk', '?')}] {seg['start']/1000:7.1f}s  {clean(seg['text'])}")
    else:
        print(" ", clean(res[0]["text"]))


if __name__ == "__main__":
    main()
