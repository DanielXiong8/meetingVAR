"""转写后处理 · 术语纠错（C5 转型方案，v3）。

实测（SETUP.md C5）：ASR 术语错误两类——
  a) 声学近音错写（测序污染→测试污染）：整句拼音序列相似度不足以判定，
     改为**滑窗切词后按词长窗口比对拼音序列**（阈值 0.78，限 ≥4 字术语，长度差 ≤2）；
  b) 拉丁词拼写漂移（kraken2→crowken二）：去数字 difflib ≥0.6，且**尾随汉字数字
     一并转成阿拉伯数字**（二→2），避免出现 "kraken2二"。
确定性替换，返回 (原文→术语) 记录供厚纪要引用。
已知局限：Q30↔「q 三十」等中西数字混合形态暂不覆盖（Issue #6）。
"""
from __future__ import annotations

import difflib
import re

from ..memory.govern import EN_COMMON

# pypinyin 懒加载：无该依赖的环境（CI model-free、最小安装）仍可跑完整转写管线，
# 术语纠错降级为恒等变换（不阻塞转写/监听/证据），并提示一次。
_lazy_pinyin = None   # None=未探测；False=不可用；callable=可用
_warned = False


def _get_pinyin():
    """返回 lazy_pinyin 可调用对象；环境缺依赖时返回 None（只提示一次）。"""
    global _lazy_pinyin, _warned
    if _lazy_pinyin is None:
        try:
            from pypinyin import lazy_pinyin
        except ImportError:
            if not _warned:
                print("[correct] 未安装 pypinyin：术语纠错已跳过（转写与证据流水不受影响）")
                _warned = True
            _lazy_pinyin = False
        else:
            _lazy_pinyin = lazy_pinyin
    return _lazy_pinyin or None

_CN_NUM = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}


def _pinyin_key(word: str) -> str:
    pinyin = _get_pinyin()
    return "".join(pinyin(word)) if pinyin else ""


def _latin_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z\-]{1,}", text)


def correct(text: str, terms: list[str]) -> tuple[str, list[dict]]:
    """确定性改写（三状态，宁漏勿误——2026-10-11 外部裁决 v1）。

    返回 (改写后文本, records)。record 字段：
      src/dst/status/reason/rule_id —— 本函数产出；
      ts/termset_id —— 本函数置 None（不可靠获取即显式未知），
                       由会话层（pipeline）用行时间戳与词表内容哈希覆盖。
      accepted        证据充分，文本已替换；
      review_required 证据不足/触发守卫，文本保持原文，候选入待审队列
                      （撤销/确认时恢复原文片段，不得反向再跑纠错器）。
      rule_id: zh_same_len / zh_len_diff / latin_accept / latin_gray_zone / latin_common_source

    分级判据（B 组 7457 处误改的对策）：
      中文：等长近音（首尾音节锚定+重合≥0.5）→ accepted；长度差 ±1 → review；
      拉丁：ratio ≥0.75 且原词非常用词 → accepted；0.6–0.75 或原词常用 → review。

    定位替换（F1）：中文改写只在命中的汉字连续段内替换**该处一次**，
    不再用 str.replace 全局替换——同段多处同形词只改第一处（宁漏勿误），
    record 与具体出现位置一一对应。等长替换不移动后续位置，遍历安全。
    """
    records: list[dict] = []
    out = text

    def _rec(src: str, dst: str, status: str, reason: str, rule_id: str) -> None:
        records.append({
            "src": src, "dst": dst, "status": status, "reason": reason,
            "rule_id": rule_id, "ts": None, "termset_id": None,
        })

    # 0) 缩写词归并（test2 实测：ASR 把 VCF 写成 "v c f"、SNV 写成 "s n v"，
    #    逐字母拆写后精确子串永远匹配不上）。仅当归并形恰为词表中的缩写词时执行
    #    （指向性替换，不做自由归一化）；右到左替换避免位移。
    if terms:
        for term in sorted(terms, key=len, reverse=True):
            core = term.strip()
            if not (2 <= len(core) <= 6 and re.fullmatch(r"[A-Za-z]+", core)):
                continue
            spaced = re.compile(
                r"(?<![A-Za-z])" + r"\s*".join(re.escape(c) for c in core) + r"(?![A-Za-z])",
                re.IGNORECASE,
            )
            for m in reversed(list(spaced.finditer(out))):
                if " " not in m.group(0):  # 未拆写形态（如已正确的 VCF）不动
                    continue
                out = out[: m.start()] + core + out[m.end():]
                _rec(m.group(0), core, "accepted", "缩写词去空格归并（ASR 逐字母拆写）", "acronym_join")

    pinyin = _get_pinyin()
    if pinyin is None or not terms:
        return out, records  # 无 pypinyin：恒等返回（降级，不阻塞证据流水）

    # a) 中文术语：汉字滑窗 + 首尾音节锚定 + 字符重合
    #    （difflib 分块匹配算不出 0.84 的编辑相似度，改用可解释判定：
    #     首音节同音 + 尾音节同音 + 字符重合≥0.5，头尾锚定天然防"前缀误报"）
    for term in terms:
        if re.fullmatch(r"[A-Za-z0-9\s\-]+", term) or len(term) < 4:
            continue
        py_term = pinyin(term)
        for m in re.finditer(r"[\u4e00-\u9fff]{2,}", out):
            run = m.group(0)
            if term in run:
                continue
            hit = None  # (段内偏移, 原词, 长度)
            for L in range(len(term) - 1, len(term) + 2):
                for i in range(0, len(run) - L + 1):
                    seg = run[i : i + L]
                    py_seg = pinyin(seg)
                    if not py_seg or not py_term:
                        continue
                    head_ok = py_seg[0] == py_term[0]
                    tail_ok = py_seg[-1] == py_term[-1]
                    if not (head_ok and tail_ok):
                        continue
                    overlap = difflib.SequenceMatcher(None, seg, term).ratio()
                    if overlap >= 0.5 and seg != term:
                        hit = (i, seg, L)
                        break
                if hit:
                    break
            if hit:
                i, seg, L = hit
                if L == len(term):
                    pos = m.start() + i
                    out = out[:pos] + term + out[pos + L:]  # 等长：不移动后续偏移
                    _rec(seg, term, "accepted", "中文等长近音（锚定+重合）", "zh_same_len")
                else:
                    _rec(seg, term, "review_required", "中文近音但长度差 ±1，宁漏勿误", "zh_len_diff")

    # b) 拉丁术语：去数字 difflib + 尾随汉字数字转阿拉伯
    for term in terms:
        if not re.search(r"[A-Za-z]", term):
            continue
        if re.search(re.escape(term), out, flags=re.IGNORECASE):
            continue
        term_core = re.sub(r"[0-9]+", "", term).lower()
        if len(term_core) < 4:
            continue
        for tok in sorted(set(_latin_tokens(out)), key=len, reverse=True):
            tok_core = re.sub(r"[0-9]+", "", tok).lower()
            ratio = difflib.SequenceMatcher(None, tok_core, term_core).ratio()
            if ratio < 0.6 or abs(len(tok_core) - len(term_core)) > 3:
                continue
            if tok_core in EN_COMMON:
                _rec(tok, term, "review_required", "原词为常用英文词（常见词互换是 B 组改写链根源）", "latin_common_source")
                break
            if ratio < 0.75:
                _rec(tok, term, "review_required", f"拉丁相似度 {ratio:.2f} 处于灰区（0.6–0.75）", "latin_gray_zone")
                break
            tail_num = ""
            m = re.search(re.escape(tok) + r"([零一二两三四五六七八九]+)", out)
            if m and re.search(r"[0-9]", term):
                tail_num = "".join(_CN_NUM.get(ch, "") for ch in m.group(1))
            replacement = term if not re.search(r"[0-9]", term) else re.sub(r"[0-9]+$", "", term) + tail_num
            out = re.sub(re.escape(tok) + r"[零一二两三四五六七八九]*", replacement, out, count=1)
            _rec(tok, replacement, "accepted", f"拉丁高相似 {ratio:.2f}", "latin_accept")
            break

    return out, records
