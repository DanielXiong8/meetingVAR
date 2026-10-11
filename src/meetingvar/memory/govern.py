"""词表治理层（GPT 裁决 2026-10-11 的第一版落地）：监听表 ≠ 纠错表。

三层状态（对应评审裁决的 watch_terms / correction_candidates / approved_corrections）：
  approved    人工添加词（用户明确意图，默认同时进入监听与纠错；#!watch 可降为仅监听）
  correction  自动抽取且通过全部守卫 → 可参与转写改写
  watch       仅监听（信息量不足以支撑"改写文本"，但有触发价值）
  excluded    连监听都不值得（标点/数字/标识符/常用词——只会制造触发噪声）

守卫原则（宁漏勿误）：
  - 硬排除：含标点/数字/下划线/URL 痕迹的词条（笔记短语、代码标识符、指标行）；
  - 常用英文词整表排除（difflib 对常用词两两相似度天然过线，是 B 组改写链的根源）；
  - 常用中文词整表排除（数据/区域/结果 这类词没有触发辨识价值）；
  - 多词短语（如 burden test / Mann-Whitney U）允许进入监听，但 v1 不参与改写
    （拉丁改写按单 token 匹配，多词目标天然匹配不上，标注 watch 是如实反映）；
  - 人工词绕过自动过滤器（用户意图优先，#!keep/#  手动词既有机制）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 常用英文词（函数词 + 高频普通名词/动词/技术词）。目标不是完备词频表，
# 而是拦住 B 组实测暴露的改写链成员与高频触发源；可用 MEETINGVAR_COMMON_WORDS 文件追加。
EN_COMMON = frozenset(
    """the a an and or but if then than that this these those there here of to in on at by for with
    from as is are was were be been being am it its he she they them their theirs him his her hers
    you your yours we our us i me my mine do does did done doing have has had having not no nor so
    too very s t can will just should would could might must shall may cannot don didnt doesn isnt
    wasnt wont about above after again against all almost along already also although always among
    another anyone anything around because become becomes been before behind below beside best
    better between both bring but came can come could did different do does done down during each
    either else enough even ever every everyone everything few first for found from get give go
    good got great group had has have he help her here him his how however i if important in into
    is it its itself keep kind know known large last later least less let like likely line little
    long look made make many may maybe me mean means might more most move much must my myself near
    need never new next no not now of off often on once one only open or other others our out over
    own part per perhaps place put rather really right same say says see seem seen several she
    should show shown side since small so some something still such take taken tell than that the
    their them themselves then there these they thing things think this those though three through
    thus to together too took toward two under until up upon us use used useful uses using usually
    various very was way we well were what when where whether which while who whole whom why will
    with within without work would yet you your
    used user using query queries message messages image images file files data dataset set sets
    input outputs output result results value values name names type types line lines list lists
    table tables text texts page pages site sites link links group groups class classes level
    levels size sizes order orders change changes changing changed process process step steps test
    tests example examples sample samples section number numbers case cases point points part
    parts time times way ways day days week weeks month months year years thing things
    framework frameworks feature features service services server servers client clients request
    requests response responses config configuration setting settings default defaults version
    versions update updates create created remove removed add added read write open close start
    stop run runs call calls return returns function functions method methods parameter parameters
    argument arguments variable variables string strings array arrays object objects task tasks
    most only reduce auto corpus config class task size most
    bash github category root association attention average down file allow max sum
    """.split()
)

# 常用中文词（无触发辨识价值；人工词不受此限）
ZH_COMMON = frozenset(
    """数据 结果 问题 情况 内容 部分 方法 方式 过程 进行 可以 我们 你们 他们 因为 所以 但是 如果
    现在 然后 这个 那个 什么 怎么 就是 还是 还有 一些 一下 自己 大家 东西 地方 时候 主要 次要
    一般 普通 常见 常用 标准 规则 说明 介绍 讨论 排除 注释 包括 如下 以下 以上 相关 有关 对于
    关于 通过 进行 使用 操作 处理 确认 检查 查看 看看 简单 复杂 重要 关键 基本 基础 逻辑 思路
    方案 计划 安排 后续 接下来 之前 之后 今天 明天 昨天 上午 下午 会议 记录 文档 文件 信息 区域
    """.split()
)

_PUNCT_HARD = set('，。；：、""''（）【】[]{}<>|/\\@#$%^&*=+~！？…—·"《》〈〉.,;:\'')
_URL_HINT = re.compile(r"https?|\.com|\.org|\.net|\.py\b|\.sh\b|\.md\b|\.json\b", re.I)


@dataclass
class GovernedTerm:
    term: str
    source: str  # manual / auto
    status: str  # approved / correction / watch / excluded
    reason: str = ""


@dataclass
class GovernResult:
    watch: list[str] = field(default_factory=list)
    correction: list[str] = field(default_factory=list)
    records: list[GovernedTerm] = field(default_factory=list)

    def stats(self) -> dict:
        c: dict[str, int] = {}
        for r in self.records:
            c[r.status] = c.get(r.status, 0) + 1
        return c


def _hard_excluded(word: str) -> str | None:
    """词形守卫：返回排除原因，None 表示通过。"""
    w = word.strip()
    if len(w) < 2:
        return "过短"
    if any(ch in _PUNCT_HARD for ch in w):
        return "含标点/括号（笔记短语或指标行）"
    if any(ch.isdigit() for ch in w):
        # 短字母数字码（S3/KRAKEN2）是真术语；带空格或过长的含数字串是日期/指标行
        letters = sum(ch.isalpha() for ch in w)
        if not (len(w) <= 6 and letters >= 1 and " " not in w):
            return "含数字（日期/指标行）"
    if "_" in w:
        return "代码标识符（下划线）"
    if _URL_HINT.search(w):
        return "URL/文件名痕迹"
    if w.startswith(("#", "-", "*")) or w.endswith("*"):
        return "markdown 痕迹"
    return None


def classify(term: str, source: str = "auto") -> GovernedTerm:
    """单个词条的治理判定。source: manual / auto。"""
    w = term.strip()
    if not w:
        return GovernedTerm(w, source, "excluded", "空词条")
    hard = _hard_excluded(w)
    if hard and source != "manual":
        return GovernedTerm(w, source, "excluded", hard)
    if source == "manual":
        return GovernedTerm(w, source, "approved", "人工添加（默认授权监听+纠错）")
    low = w.lower()
    is_latin = bool(re.fullmatch(r"[A-Za-z][A-Za-z\s\-]*", w))
    if is_latin and low in EN_COMMON:
        return GovernedTerm(w, source, "excluded", "常用英文词")
    if not is_latin and w in ZH_COMMON:
        return GovernedTerm(w, source, "excluded", "通用中文词")
    if is_latin and " " in w:
        return GovernedTerm(w, source, "watch", "多词短语：仅监听（v1 改写按单 token 匹配）")
    has_latin = bool(re.search(r"[A-Za-z]", w))
    has_zh = bool(re.search(r"[\u4e00-\u9fff]", w))
    if has_zh and not has_latin and len(w) < 4:
        return GovernedTerm(w, source, "watch", "中文短词：改写需 ≥4 字")
    return GovernedTerm(w, source, "correction", "通过全部守卫")


def govern_terms(terms: dict[str, list[str]] | list[str], manual: set[str] | None = None) -> GovernResult:
    """治理整张词表。terms 支持 dict（带来源标签）或 list。manual 中的词按人工词处理。"""
    manual = manual or set()
    items: list[tuple[str, str]] = []
    if isinstance(terms, dict):
        for w, srcs in terms.items():
            src = "manual" if w in manual or any("手动" in s for s in srcs) else "auto"
            items.append((w, src))
    else:
        for w in terms:
            items.append((w, "manual" if w in manual else "auto"))

    res = GovernResult()
    seen: set[str] = set()
    for w, src in items:
        if w in seen:
            continue
        seen.add(w)
        g = classify(w, src)
        res.records.append(g)
        if g.status in {"approved", "correction"}:
            res.correction.append(w)
        if g.status in {"approved", "correction", "watch"}:
            res.watch.append(w)
    return res
