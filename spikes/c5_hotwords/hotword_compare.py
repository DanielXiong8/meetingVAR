"""Spike C5 · ASR 热词增强对比实验。

实验设计：
  - 语料：C2 端到端测试采集的 loopback 音频（TTS 经扬声器→空气→系统回路，
    已实测把「测序污染」劣化成「测试污染」——天然实验靶子）
  - 词表：从仿真项目术语表（spikes/b1_rag/sample_project/术语表.md）取词，
    模拟真实管线"项目记忆 → 术语表 → ASR 热词"
  - 对照：同一音频，开/关热词各转写一次，统计目标术语的正确命中数

用法：
    python hotword_compare.py --wav data/clips/loopback_test.wav [--device cuda]
输出：终端对照表 + data/clips/c5_report.json（进 PPT 的数字来源）
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

TERMS = ["测序污染", "index hopping", "kraken2", "UMI", "重测", "Q30", "duplication"]
MODELS_ROOT = Path("D:/env/models")


def find_model_dir(keyword: str) -> str:
    hits = [
        p.parent
        for p in list(MODELS_ROOT.rglob("config.yaml")) + list(MODELS_ROOT.rglob("configuration.json"))
        if keyword.lower() in str(p).lower()
    ]
    if not hits:
        raise FileNotFoundError(f"未找到含 {keyword!r} 的模型目录")
    return str(hits[0])


def clean(t: str) -> str:
    return re.sub(r"<\|[^|]*\|>", "", t)


def hit(term: str, text: str) -> bool:
    return term.lower() in text.lower()


def build_model(engine: str, device: str):
    from funasr import AutoModel

    if engine == "seaco":
        asr_dir = find_model_dir("seaco_paraformer")
    else:
        asr_dir = find_model_dir("SenseVoiceSmall")
    return AutoModel(
        model=asr_dir,
        vad_model=find_model_dir("fsmn_vad"),
        spk_model=find_model_dir("campplus"),
        punc_model=find_model_dir("punc_ct-transformer"),
        device=device,
        disable_update=True,
        disable_pbar=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="data/clips/loopback_test.wav")
    ap.add_argument("--device", default="cuda", choices=["cpu", "cuda"])
    ap.add_argument("--engine", default="sensevoice", choices=["sensevoice", "seaco"])
    args = ap.parse_args()

    from funasr import AutoModel

    model = build_model(args.engine, args.device)

    t0 = time.time()
    base = clean(model.generate(input=args.wav, batch_size_s=60)[0]["text"])
    t_base = time.time() - t0

    t0 = time.time()
    hw = clean(model.generate(input=args.wav, batch_size_s=60, hotword=" ".join(TERMS))[0]["text"])
    t_hw = time.time() - t0

    print(f"\n== 基线（无热词）  {t_base:.1f}s ==")
    print(base)
    print(f"\n== 热词版（{len(TERMS)} 词）  {t_hw:.1f}s ==")
    print(hw)

    print("\n== 术语命中对照 ==")
    print(f"{'术语':<18}{'基线':^6}{'热词':^6}")
    for term in TERMS:
        print(f"{term:<18}{'✓' if hit(term, base) else '✗':^6}{'✓' if hit(term, hw) else '✗':^6}")
    n_base = sum(hit(t, base) for t in TERMS)
    n_hw = sum(hit(t, hw) for t in TERMS)
    print(f"命中合计: 基线 {n_base}/{len(TERMS)} → 热词 {n_hw}/{len(TERMS)}")

    report = {
        "wav": args.wav,
        "engine": args.engine,
        "device": args.device,
        "terms": TERMS,
        "baseline_text": base,
        "hotword_text": hw,
        "hits_baseline": n_base,
        "hits_hotword": n_hw,
        "seconds_baseline": round(t_base, 2),
        "seconds_hotword": round(t_hw, 2),
    }
    out = Path(f"data/clips/c5_report_{args.engine}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
