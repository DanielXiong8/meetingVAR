"""LLM 选词人模式（2026-10-12 C 路线定案）。

用户裁决：大项目的关键词就 10~20 个（像论文 Keywords），不是几千个。
旧漏斗（L0/L1 宽进 + 治理兜底）在 test1 B 组被证伪：7457 处误改。
新路线：LLM 直接当"选词人"——L0/L1 降级为给 LLM 的参考资料，产出直接是精选词；
数量上限 TERM_CAP（默认 20）硬执行，随项目推进由 rescan/反馈缓慢演化。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

TERM_CAP = int(os.environ.get("MEETINGVAR_TERM_CAP", "20"))

_CURATE_PROMPT = """你是这个项目的资深成员，正在为会议取证系统挑选「热词」——
只有当这些词在会上被说出时，系统才知道该重点取证并保存片段。

<项目资料>
{material}
</项目资料>

请挑选 **10~{cap} 个**本项目最核心、最可能被大家口头讨论的关键词。
挑选标准（像给论文写 Keywords，而不是做全文索引）：
1. 只要"会被口头说出"的词：方法/模型/系统名、专业术语、基因/位点等实体、核心人名；
2. 排除：代码函数名/变量名、常见日常词、数字与指标、文档标题、聊天客套；
3. 宁缺毋滥：10 个以下说明你读得不够深；超过 {cap} 个会被截断；
4. 每个词给一句话理由：为什么这个项目开会时大家会说到它。

只输出 JSON（不要 markdown 代码块）：
{{"keywords": [{{"term": "词", "why": "理由"}}], "persons": [{{"name": "人名", "role": "角色", "why": "依据"}}]}}
红线：term 必须是资料中出现过的词形；不得编造。
/no_think"""


def _reference_material(memory_dir: Path | None, repo_dir: Path | None) -> str:
    """给选词人的参考资料：文档摘录（预算内）+ 代码高频定义清单（只作上下文，不作产出）。"""
    parts: list[str] = []
    if memory_dir and Path(memory_dir).exists():
        budget = 30000
        for f in sorted(Path(memory_dir).rglob("*")):
            if f.suffix.lower() not in {".md", ".txt"} or budget <= 0:
                continue
            body = f.read_text(encoding="utf-8", errors="ignore")[:4000]
            parts.append(f"[文档 {f.relative_to(memory_dir)}]\n{body}")
            budget -= len(body)
    if repo_dir and Path(repo_dir).exists():
        symbols: list[str] = []
        for py in sorted(Path(repo_dir).rglob("*.py"))[:200]:
            try:
                src = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            found = re.findall(
                r"^def ([A-Za-z_][A-Za-z0-9_]{3,})|^class ([A-Z][A-Za-z0-9_]{2,})", src, re.M
            )
            symbols += [a or b for a, b in found]
        if symbols:
            parts.append("[代码高频定义（仅供参考上下文，不是热词）]\n" + ", ".join(symbols[:80]))
    return "\n\n---\n\n".join(parts)


def curate_terms(memory_dir: Path | None, repo_dir: Path | None = None) -> dict | None:
    """LLM 选词人：产出 10~TERM_CAP 个核心关键词（带理由）+ 人物名单。

    返回 {"keywords": [{"term","why"}], "persons": [...], "termset_id": str}；
    LLM 未配置/解析失败返回 None（上层回落旧路线）。
    """
    from ..llm import chat

    material = _reference_material(memory_dir, repo_dir)
    if not material.strip():
        return None
    prompt = _CURATE_PROMPT.format(material=material, cap=TERM_CAP)
    for _ in range(2):  # 小模型 JSON 不稳：剥栅栏 + 重试一次（与 llm_extract 同策略）
        raw = chat(prompt)
        if raw is None:
            return None
        cleaned = re.sub(r"```(?:json)?|```", "", raw)
        m = re.search(r"\{.*\}", cleaned, re.S)
        if not m:
            continue
        try:
            data = json.loads(m.group())
        except ValueError:
            continue
        terms: list[str] = []
        whys: dict[str, str] = {}
        for k in data.get("keywords") or []:
            w = str(k.get("term", "")).strip() if isinstance(k, dict) else str(k).strip()
            if w and w not in terms:
                terms.append(w)
                whys[w] = str(k.get("why", "")) if isinstance(k, dict) else ""
        terms = terms[:TERM_CAP]  # 上限硬执行
        persons = [p for p in (data.get("persons") or []) if isinstance(p, dict) and p.get("name")]
        joined = "\n".join(sorted(terms))
        return {
            "keywords": [{"term": w, "why": whys[w]} for w in terms],
            "persons": persons,
            "termset_id": hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12] if terms else "",
        }
    return None
