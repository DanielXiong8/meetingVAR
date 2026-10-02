"""Spike B1 · 项目记忆 RAG 雏形（零新增依赖：jieba + scikit-learn，均随 FunASR 环境已装）。

这是 meetingVAR「会前兴趣注入（F1）」和「系统二深读（报告引用溯源）」的检索底座 v0：
  - 索引：项目文件夹下所有 .md/.txt，jieba 分词 → TF-IDF
  - 检索：query 分词 → 余弦相似度 Top-K 段落（按空行分段的粗粒度段落）
  - 输出：命中文件、段落、得分——厚纪要引用溯源的雏形

用法：
    python rag_demo.py --query "S3 为什么怀疑测序污染，怎么排查"
"""
from __future__ import annotations

import argparse
from pathlib import Path

import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

SAMPLE = Path(__file__).parent / "sample_project"


def load_passages(root: Path) -> list[tuple[str, int, str]]:
    """读所有 md/txt，按空行切段。返回 (文件名, 段落序号, 段落文本)。"""
    out = []
    for f in sorted(root.rglob("*")):
        if f.suffix.lower() not in {".md", ".txt"}:
            continue
        text = f.read_text(encoding="utf-8")
        for i, para in enumerate(p.strip() for p in text.split("\n\n")):
            para = " ".join(line.strip().lstrip("#-*> [").strip() for line in para.splitlines()).strip()
            if len(para) >= 15:
                out.append((f.name, i, para))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--topk", type=int, default=3)
    args = ap.parse_args()

    passages = load_passages(SAMPLE)
    docs = [" ".join(jieba.cut(p[2])) for p in passages]
    vec = TfidfVectorizer()
    m = vec.fit_transform(docs)
    q = vec.transform([" ".join(jieba.cut(args.query))])
    scores = cosine_similarity(q, m)[0]

    ranked = sorted(zip(scores, passages), key=lambda x: -x[0])[: args.topk]
    print(f"[query] {args.query}\n[corpus] {len(passages)} passages from {SAMPLE.name}/\n")
    for s, (fname, idx, text) in ranked:
        print(f"  {s:.3f}  {fname}#p{idx}")
        print(f"          {text[:110]}{'…' if len(text) > 110 else ''}\n")


if __name__ == "__main__":
    main()
