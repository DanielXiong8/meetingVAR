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

from pypinyin import lazy_pinyin

_CN_NUM = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}


def _pinyin_key(word: str) -> str:
    return "".join(lazy_pinyin(word))


def _latin_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z\-]{1,}", text)


def correct(text: str, terms: list[str]) -> tuple[str, list[tuple[str, str]]]:
    fixes: list[tuple[str, str]] = []
    out = text

    # a) 中文术语：汉字滑窗 + 首尾音节锚定 + 字符重合
    #    （difflib 分块匹配算不出 0.84 的编辑相似度，改用可解释判定：
    #     首音节同音 + 尾音节同音 + 字符重合≥0.5，头尾锚定天然防"前缀误报"）
    for term in terms:
        if re.fullmatch(r"[A-Za-z0-9\s\-]+", term) or len(term) < 4:
            continue
        py_term = lazy_pinyin(term)
        for run in set(re.findall(r"[\u4e00-\u9fff]{2,}", out)):
            if term in run:
                continue
            for L in range(len(term) - 1, len(term) + 2):
                for i in range(0, len(run) - L + 1):
                    seg = run[i : i + L]
                    py_seg = lazy_pinyin(seg)
                    if not py_seg or not py_term:
                        continue
                    head_ok = py_seg[0] == py_term[0]
                    tail_ok = py_seg[-1] == py_term[-1]
                    if not (head_ok and tail_ok):
                        continue
                    overlap = difflib.SequenceMatcher(None, seg, term).ratio()
                    if overlap >= 0.5 and seg != term:
                        out = out.replace(seg, term)
                        fixes.append((seg, term))
                        break

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
            if difflib.SequenceMatcher(None, tok_core, term_core).ratio() >= 0.6 and abs(len(tok_core) - len(term_core)) <= 3:
                tail_num = ""
                m = re.search(re.escape(tok) + r"([零一二两三四五六七八九]+)", out)
                if m and re.search(r"[0-9]", term):
                    tail_num = "".join(_CN_NUM.get(ch, "") for ch in m.group(1))
                replacement = term if not re.search(r"[0-9]", term) else re.sub(r"[0-9]+$", "", term) + tail_num
                out = re.sub(re.escape(tok) + r"[零一二两三四五六七八九]*", replacement, out, count=1)
                fixes.append((tok, replacement))
                break

    return out, fixes
