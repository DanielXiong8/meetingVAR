# -*- coding: utf-8 -*-
"""Fill the 2026 SDG Pitch template with meetingVAR content.

Template: competition/2026SDG-Pitch-template.pptx  (14 slides, 10 x 5.62 in)
Output:   competition/meetingVAR_2026SDG_Pitch.pptx

Layout rules learned from rendering the template:
  * logo strip occupies y 0.24..0.70 in, horizontal rule at ~0.92 in
  * every slide title starts at y = 1.00 in, body at y = 1.42 in
  * card text must be left aligned; card height derived from line count
"""
import math

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

TEMPLATE = "competition/2026SDG-Pitch-template.pptx"
OUT = "competition/meetingVAR_2026SDG_Pitch.pptx"

FONT = "等线"
INK = RGBColor(0x26, 0x2A, 0x33)
SUB = RGBColor(0x5A, 0x62, 0x70)
TEAL = RGBColor(0x0F, 0x6B, 0x73)
CARD_FILL = "F4F6F8"
CARD_LINE = "D8DDE3"

TITLE_Y = 1.00
BODY_Y = 1.42

prs = Presentation(TEMPLATE)
S = list(prs.slides)


# ------------------------------------------------------------------ helpers
def find(slide, name):
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    raise KeyError(name)


def drop(slide, *ids):
    for sh in [x for x in slide.shapes if x.shape_id in ids]:
        sh._element.getparent().remove(sh._element)


def drop_named(slide, *names):
    for sh in [x for x in slide.shapes if x.name in names]:
        sh._element.getparent().remove(sh._element)


def style_run(r, size, color=INK, bold=False, italic=False):
    f = r.font
    f.name = FONT
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.rgb = color
    rPr = r._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", FONT)


def set_text(tf, blocks, align=PP_ALIGN.LEFT):
    tf.word_wrap = True
    for p in list(tf.paragraphs)[1:]:
        p._p.getparent().remove(p._p)
    first = tf.paragraphs[0]
    for r in list(first.runs):
        r._r.getparent().remove(r._r)
    for i, block in enumerate(blocks):
        p = first if i == 0 else tf.add_paragraph()
        opts, runs = (block, block["runs"]) if isinstance(block, dict) else ({}, block)
        p.alignment = opts.get("align", align)
        if "space_before" in opts:
            p.space_before = Pt(opts["space_before"])
        if "space_after" in opts:
            p.space_after = Pt(opts["space_after"])
        for spec in runs:
            text, size = spec[0], spec[1]
            color = spec[2] if len(spec) > 2 else INK
            bold = spec[3] if len(spec) > 3 else False
            italic = spec[4] if len(spec) > 4 else False
            r = p.add_run()
            r.text = text
            style_run(r, size, color, bold, italic)


def textbox(slide, x, y, w, h, blocks, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    set_text(tf, blocks, align)
    return tb


def card_h(width, body, body_size=9.5, title=True, tag=None):
    """Height needed for a card: wrap each line to the card width, then sum rows.

    CJK glyphs are ~1 em wide, Latin ~0.55 em, so estimate row count by
    weighted character count against characters-per-line.
    """
    cpl = max(8.0, (width - 0.30) * 72.0 / body_size)
    rows = 0
    lines = list(body) + ([tag] if tag else [])
    for ln in lines:
        units = sum(1.0 if ord(c) > 127 else 0.55 for c in ln)
        rows += max(1, math.ceil(units / cpl))
    h = rows * (body_size * 1.42 / 72.0) + 0.22
    if title:
        h += 0.28
    return round(h, 2)


def card(slide, x, y, w, h, title, body, title_size=11, body_size=9.5,
         anchor=MSO_ANCHOR.TOP, tag=None):
    if h is None:
        h = card_h(w, body, body_size, bool(title), tag)
    sp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.adjustments[0] = 0.05
    sp.fill.solid()
    sp.fill.fore_color.rgb = RGBColor.from_string(CARD_FILL)
    sp.line.color.rgb = RGBColor.from_string(CARD_LINE)
    sp.line.width = Pt(0.75)
    sp.shadow.inherit = False
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = Pt(10); tf.margin_right = Pt(10)
    tf.margin_top = Pt(8); tf.margin_bottom = Pt(6)
    blocks = [{"runs": [(title, title_size, TEAL, True)], "space_after": 3}]
    for ln in body:
        blocks.append({"runs": [(ln, body_size, INK)], "space_after": 2})
    if tag:
        blocks.append({"runs": [(tag, body_size - 1, SUB, False, True)], "space_before": 3})
    set_text(tf, blocks)
    return sp


def title(slide, zh, en=""):
    runs = [(zh, 18, INK, True)]
    if en:
        runs.append((u"　" + en, 11, SUB, False, True))
    textbox(slide, 0.42, TITLE_Y, 9.2, 0.38, [{"runs": runs}])


def card_grid(slide, y, x0, w, gap, items, title_size=11, body_size=9.5,
              row_gap=0.18, per_row=None, fill_to=None):
    """Draw cards in a grid, equal height per row; return the bottom y.

    items: list of (title, body_lines) or (title, body_lines, tag).
    fill_to: stretch rows evenly so the grid bottom reaches this y.
    """
    n = per_row or len(items)
    rows = [items[i:i + n] for i in range(0, len(items), n)]
    hs = [max(card_h(w, it[1], body_size, True, it[2] if len(it) > 2 else None)
              for it in row) for row in rows]
    if fill_to:
        spare = fill_to - (y + sum(hs) + row_gap * (len(rows) - 1))
        if spare > 0:
            hs = [h + spare / len(rows) for h in hs]
    cur = y
    for row, h in zip(rows, hs):
        for j, it in enumerate(row):
            tag = it[2] if len(it) > 2 else None
            card(slide, x0 + j * (w + gap), cur, w, h, it[0], it[1],
                 title_size, body_size, tag=tag, anchor=MSO_ANCHOR.MIDDLE)
        cur += h + row_gap
    return cur - row_gap


def lead(slide, text, y=1.38, size=11.5):
    textbox(slide, 0.42, y, 9.15, 0.32, [{"runs": [(text, size, TEAL, True)]}])


def note(slide, text_line):
    slide.notes_slide.notes_text_frame.text = text_line


# ---------------------------------------------------------------- 1 cover
s = S[0]
textbox(s, 0.45, 0.90, 5.0, 0.62, [{"runs": [(u"meetingVAR", 27, INK, True)]}])
textbox(s, 0.45, 1.30, 5.1, 0.30, [
    {"runs": [(u"AI PC 上的个人证据 Agent　|　别人帮你记下来，我们帮你证出来", 10, TEAL, True)]},
])
textbox(s, 0.45, 4.60, 5.2, 0.75, [
    {"runs": [(u"SDG Open Hack 2026 · Hack16 · 联想分赛场", 9.5, SUB)]},
    {"runs": [(u"企业命题 1：AI 办公任务助手　|　团队：（队名 · 成员 · 日期）", 9.5, SUB)], "space_before": 2},
])
note(s, u"开场 15 秒：我们不是一个更好的会议纪要工具。我们做的是会议证据层——"
        u"别人帮你记下来，meetingVAR 帮你证出来。")

# ---------------------------------------------------------------- 2 deal summary
s = S[1]
set_text(find(s, "文本框 2").text_frame, [[(u"一句话项目小结 (Deal Summary)", 16, INK, True)]])
shp = find(s, "Text Placeholder 1")
shp.height = Inches(2.75)
set_text(shp.text_frame, [
    {"runs": [(u"meetingVAR 是一套装在 Windows 上的会议证据 Agent：", 14, INK, True)]},
    {"runs": [(u"平时不落盘，只在会议内容命中你的项目关注点时，保存前后一段音频 + 屏幕 + 转写；"
               u"会后能找回原话、看到谁改了口，并导出一份可校验的证据包。", 12, INK)], "space_before": 6},
    {"runs": [(u"对应 SDG 16 和平、正义与强大机构", 11.5, TEAL, True),
               (u"——让组织里的每一句承诺可核验、可追责（目标 16.6 / 16.7）。", 11.5, SUB)], "space_before": 10},
    {"runs": [(u"别人帮你记下来，meetingVAR 帮你证出来。", 12, TEAL, True)], "space_before": 10},
])
note(s, u"一句话小结：写清三件事——本地运行、选择性取证、可校验。"
        u"评委如果只记住一句，希望是最后那句。")

# ---------------------------------------------------------------- 3 team
s = S[2]
set_text(find(s, "文本框 5").text_frame, [
    [(u"核心团队 Team", 18, INK, True)],
    {"runs": [(u"三人分工：核心链路 / 界面 / 验证与现场，一人一段，不重叠", 10, SUB)], "space_before": 2},
])
drop(s, 7, 8, 9, 10, 11)      # template stock avatars
drop_named(s, "文本框 12", "文本框 13", "文本框 14", "文本框 15", "文本框 16")

team = [
    (u"（姓名）", u"队长 · 核心开发",
     [u"技能：系统架构 / 全栈（ASR·RAG·Agent）", u"经验：把采集到证据包的整条链路做通", u"动机：自己就吃过“纪要漏掉关键句”的亏"]),
    (u"（姓名）", u"UI 前端",
     [u"技能：界面设计与前端实现", u"经验：深色主题 / 离线可用 / 无障碍可读", u"动机：让证据一眼看得懂、点得到"]),
    (u"（姓名）", u"QA · 演示",
     [u"技能：测试设计 / 数据准备 / 现场编排", u"经验：设备清单、测试矩阵、fallback 录屏", u"动机：现场一次都不许出错"]),
]
for i, (name, role, lines) in enumerate(team):
    x = 0.45 + i * 3.12
    ph = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(1.62), Inches(1.05), Inches(1.05))
    ph.fill.solid()
    ph.fill.fore_color.rgb = RGBColor.from_string("E7EDF0")
    ph.line.color.rgb = RGBColor.from_string("CBD5DB")
    ph.line.width = Pt(0.75)
    ph.shadow.inherit = False
    tf = ph.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    set_text(tf, [{"runs": [(u"照片", 8.5, SUB)], "align": PP_ALIGN.CENTER}])
    textbox(s, x, 2.85, 2.8, 1.95, [
        {"runs": [(name + u"　", 10.5, INK, True), (role, 10.5, TEAL, True)]},
    ] + [{"runs": [(ln, 9, SUB)], "space_before": 3} for ln in lines])
textbox(s, 0.45, 4.98, 9.1, 0.3, [
    {"runs": [(u"提交前把圆形占位换成真实照片、填上姓名即可；版式已按 3 人排好。", 8.5, SUB, False, True)]},
])
note(s, u"团队页 20 秒：不要念简历，只说三个人各自解决了哪类风险——"
        u"核心负责链路能跑通，前端负责评委看得见，QA 负责现场不出事故。")

# ---------------------------------------------------------------- 4 problem
s = S[3]
title(s, u"问题：AI 纪要很顺，但漏掉最重要的 1%", "What problem is solved?")
drop_named(s, "文本框 1")
drop(s, 4, 5, 6, 7, 8)
tb = find(s, "文本框 2")
tb.left, tb.top = Inches(0.42), Inches(1.40)
tb.width, tb.height = Inches(5.2), Inches(3.9)
set_text(tb.text_frame, [
    {"runs": [(u"开完会，你最想要的不是又一段摘要，而是那 30 秒：", 12, INK, True)]},
    {"runs": [(u"· 会上 PI 随口一句“那篇论文可能影响你的课题”，纪要里没有；", 10.5, INK)], "space_before": 6},
    {"runs": [(u"· 上周说好周五交付，这周谁改成了下周三？原话找不回来；", 10.5, INK)], "space_before": 4},
    {"runs": [(u"· 全量录屏两小时，你找不到；平台回放还没开。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"方案：把足球 VAR 的思路搬进会议——选择性取证。", 12, TEAL, True)], "space_before": 14},
    {"runs": [(u"平时不落盘；命中你的项目关注点（热词 / 语义 / 手动）时，", 10.5, SUB)], "space_before": 5},
    {"runs": [(u"才保存前后证据片段：声音 + 屏幕 + 原话 + 时间戳。", 10.5, SUB)]},
    {"runs": [(u"全场薄纪要，和你有关的厚证据。", 10.5, SUB, True)], "space_before": 5},
])
caps = [
    (u"① 证据包 Evidence Pack", [u"命中片段打包 ZIP：音频、屏幕帧、转写、manifest、SHA256。",
                              u"点一次“校验”，全部文件哈希逐项对上。"]),
    (u"② 承诺账本 Commitment Ledger", [u"跨会议追踪：谁承诺什么、谁改了口、哪场会、原话是什么。",
                                     u"变更以 diff 呈现：周五 → 下周三。"]),
    (u"③ 找回 30 秒 Find Moment", [u"自然语言定位 30–90 秒片段：时间、说话人、原句、屏幕帧。",
                                  u"点开即播，不用拖动两小时进度条。"]),
]
cur = 1.40
for t, body in caps:
    h = card_h(3.75, body, 9, True)
    card(s, 5.85, cur, 3.75, h, t, body, 10.5, 9)
    cur += h + 0.18

# VAR flow strip, bottom-left: nothing recorded -> hit -> evidence
flow_y = 3.72
steps = [
    (u"平时不落盘", u"没有命中就没有文件"),
    (u"命中关注点", u"热词 / 语义 / 手动 VAR"),
    (u"保存前后片段", u"声音 + 屏幕 + 原话"),
]
for i, (t, s2) in enumerate(steps):
    sp = card(s, 0.42 + i * 1.78, flow_y, 1.62, 0.92, t, [s2], 10, 8)
    if i < 2:
        textbox(s, 0.42 + i * 1.78 + 1.62, flow_y + 0.26, 0.16, 0.3,
                [{"runs": [(u"→", 12, TEAL, True)], "align": PP_ALIGN.CENTER}])
textbox(s, 0.42, flow_y + 1.02, 5.3, 0.3, [
    {"runs": [(u"这就是 VAR：只在值得回看的地方留证据，不做全量监控。", 9, SUB, False, True)]},
])
note(s, u"痛点页 40 秒：先给一个所有人都经历过的具体场景（纪要漏掉一句），"
        u"再说全量录屏也救不了你，最后落到“选择性取证”这个词。")

# ---------------------------------------------------------------- 5 target customer
s = S[4]
title(s, u"目标客户 Target Customer")
drop_named(s, "文本框 1")
drop_named(s, "矩形 8")
shp = find(s, "Subtitle 17")
shp.left, shp.top = Inches(0.42), Inches(1.36)
shp.width, shp.height = Inches(9.15), Inches(0.32)
set_text(shp.text_frame, [
    {"runs": [(u"谁最需要“被证明”，就卖给谁——他们都在为“找不回原话”付代价。", 11.5, TEAL, True)]},
])
cust = [
    (u"科研课题组 / 学生", [u"组会信息密度高，PI 一句话影响课题；", u"讲座 2 小时，考点只占几分钟。"],
     u"痛点：关键那句，期末复习时全靠回忆。"),
    (u"PMO / 咨询 / 投行", [u"承诺多、变更快、要追责；", u"改一次口，两周排期就废。"],
     u"痛点：日期被口头改过三次，没人认账。"),
    (u"法务 / 合规 / 采购", [u"需要“谁在什么时候说了什么”，", u"且数据不能出本机。"],
     u"痛点：取证要原始记录，云回放会过期。"),
    (u"销售 / 客户成功", [u"客户口头承诺与需求变更，", u"是纠纷与扯皮的源头。"],
     u"痛点：客户说“我早就说过”，你翻不出来。"),
]
bottom = card_grid(s, 1.78, 0.42, 4.5, 0.16, cust, 11.5, 10, per_row=2, fill_to=4.70)
textbox(s, 0.42, bottom + 0.22, 9.1, 0.3, [
    {"runs": [(u"切入路径：先科研与个人效率场景（自己就是用户、反馈快），再向有合规需求的企业团队扩展。", 9, SUB)]},
])
note(s, u"客户页 30 秒：四类人的共同点不是行业，而是“必须证明发生过什么”。"
        u"先说自己所在的课题组——这是最真实的早期用户。")

# ---------------------------------------------------------------- 6 market
s = S[5]
title(s, u"市场规模 Total Market Opportunity")
drop(s, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 17, 18)
drop_named(s, "文本框 1")
nums = [
    (u"全球 AI 会议助手", u"约 35 亿美元", u"2025 年",
     [u"Grand View Research：2025 年约 35 亿美元；", u"Market Research Future 口径 35.03 亿美元。"]),
    (u"十年增长（MRFR）", u"约 343 亿美元", u"2035 年预测",
     [u"MRFR 预测 2035 年达 342.8 亿美元，CAGR 25.62%；", u"Research and Markets 另给 2026–2030 CAGR 24.3%。"]),
    (u"中国协同办公", u"587 亿元", u"2025 年",
     [u"共研产业研究院：2025 年中国协同办公产值 587.36 亿元；", u"“可信会议证据”是该市场里尚未被占据的一环。"]),
]
for i, (t, big, when, lines) in enumerate(nums):
    sp = card(s, 0.42 + i * 3.16, 1.36, 3.0, 1.95, t, [])
    tf = sp.text_frame
    p = tf.add_paragraph(); p.space_before = Pt(2)
    r = p.add_run(); r.text = big; style_run(r, 19, TEAL, True)
    p = tf.add_paragraph()
    r = p.add_run(); r.text = when; style_run(r, 8, SUB, False, True)
    for ln in lines:
        p = tf.add_paragraph(); p.space_before = Pt(4)
        r = p.add_run(); r.text = ln; style_run(r, 8.5, INK)
textbox(s, 0.42, 3.50, 9.15, 0.55, [
    {"runs": [(u"我们的细分：", 10.5, INK, True),
              (u"不抢“纪要工具”的钱，做 AI PC 上的“证据层”——面向必须留住原话、且数据不能出机的人。", 10.5, INK)]},
])
textbox(s, 0.42, 4.12, 9.15, 0.9, [
    {"runs": [(u"为什么这个细分现在成立：", 10, INK, True)]},
    {"runs": [(u"① 本地算力够了：AI PC 能离线跑 ASR 与大模型；② 隐私合规收紧：数据不愿出机；"
               u"③ 会议仍是承诺产生的主要场所，但没有任何产品负责“证明”。", 9, SUB)], "space_before": 3},
])
textbox(s, 0.42, 5.05, 9.15, 0.45, [
    {"runs": [(u"数据来源：Grand View Research《AI Meeting Assistant Market 2026-2033》；Market Research Future"
               u"（AI Meeting Assistants Market, 2035）；Research and Markets《AI-Powered Meeting Assistants Market Report 2026》"
               u"（2026-02）；共研产业研究院（2025-09）。检索日期 2026-11，均为公开摘要口径。", 7, SUB)]},
])
note(s, u"市场页 30 秒：只讲三个数字和一句结论。数字都标了机构和时间，"
        u"评委追问就翻来源；我们不做“万亿市场”这种没有任何出处的说法。")

# ---------------------------------------------------------------- 7 value proposition
s = S[6]
title(s, u"价值主张 Value Proposition")
drop_named(s, "Shape 95")
drop_named(s, "矩形 3")
lead(s, u"普通 AI 纪要追求“写得顺”，meetingVAR 追求“找得到原话、对得上人、经得起回看”。")
rows = [
    (u"省时间", [u"Find Moment：自然语言秒级定位原话，带时间戳、", u"说话人、屏幕帧；不用倒 2 小时录像。"]),
    (u"省扯皮", [u"承诺账本：周五 → 下周三的每次改口都有 diff 和", u"原句可跳，追责不用翻聊天记录。"]),
    (u"带得走的证据", [u"证据包：ZIP + manifest + 每个文件 SHA256，", u"离线一键校验（证明完整性，非司法鉴定）。"]),
    (u"隐私不妥协", [u"选择性取证：不命中不落盘；本地 ASR / 模型 / 索引，", u"离线可用、数据不出机。"]),
]
bottom = card_grid(s, 1.78, 0.42, 4.5, 0.16, rows, 11.5, 10, per_row=2, fill_to=4.70)
textbox(s, 0.42, bottom + 0.22, 9.1, 0.3, [
    {"runs": [(u"定性收益：把“会后扯皮 / 重复开会确认”变成“一次采集、随时证明”，改善团队协作与信任。", 9, SUB)]},
])
note(s, u"价值页 25 秒：四个词——省时间、省扯皮、带得走、不出机。每个词配一个动作，不要展开技术。")

# ---------------------------------------------------------------- 8 partners
s = S[7]
title(s, u"关键支撑资源与伙伴 Resources / Partners")
drop_named(s, "文本框 1")
drop_named(s, "Text Placeholder 1")
groups = [
    (u"联想 AI PC（本次赛题）",
     [u"本地算力：离线 ASR、索引、知识图谱；", u"数据不出机就是产品卖点；",
      u"Magic Bay-ready：标准 UVC 摄像头 + 任意副屏 HUD。"],
     u"关系：赛题提供方与首个渠道。"),
    (u"开源与模型生态",
     [u"SenseVoice / SeACo 等本地 ASR、BGE 向量、", u"VAD、CAM++ 说话人；",
      u"云端模型只做显式开启的增强。"],
     u"关系：上游依赖，可替换、可离线。"),
    (u"用户与社区",
     [u"第一批用户 = 科研课题组 + 学生（我们自己）；", u"与早期用户共建评测集与场景脚本；",
      u"再借联想渠道与企业 fleet 走向行业版。"],
     u"关系：共创，不是单向推销。"),
]
bottom = card_grid(s, 1.36, 0.42, 3.0, 0.16, groups, 10.5, 9, fill_to=3.10)
textbox(s, 0.42, bottom + 0.30, 9.15, 0.9, [
    {"runs": [(u"成长路径：", 10, INK, True),
              (u"个人工具 → 课题组 / 小团队共享项目记忆 → 律所、咨询、科研的行业版 → OEM 预装。", 10, INK)]},
    {"runs": [(u"社区做法：把评测脚本、场景脚本开源；用户贡献的“改口案例”反过来变成测试用例。", 9, SUB)],
     "space_before": 4},
])
note(s, u"资源页 20 秒：强调我们依赖的是“标准接口 + 开源模型”，"
        u"所以不赌任何一家的私有协议——这也是风险页的伏笔。")

# ---------------------------------------------------------------- 9 tech / MVP
s = S[8]
title(s, u"技术特点与 MVP 现状", "Why us / MVP")
drop_named(s, "文本框 1")
drop_named(s, "Text Placeholder 1")
lead(s, u"不是套壳总结：一个已经跑通的 Windows 桌面应用，不是 PPT 原型。")
feats = [
    (u"选择性取证管线",
     [u"系统声音 + 麦克风 + 屏幕三源采集；", u"热词 / 语义 / 手动三层触发；",
      u"命中才保存前后片段，零命中不落盘。"]),
    (u"项目记忆 + 时序知识图谱",
     [u"Markdown 事实源 + FTS5 + BGE 向量混合检索；", u"SQLite 属性图：带生效时间与原句证据；",
      u"回答必须带可校验引用。"]),
    (u"证据三技能（后端完成）",
     [u"Evidence Pack / Commitment Ledger / Find Moment；", u"后端与 CLI 已完成并有测试覆盖；",
      u"桌面端正在把能力做成可点界面。"]),
]
bottom = card_grid(s, 1.80, 0.42, 3.0, 0.16, feats, 10.5, 9, fill_to=3.30)
textbox(s, 0.42, bottom + 0.34, 9.15, 1.5, [
    {"runs": [(u"可复现的工程基线（不是宣传数字）：", 10.5, INK, True)]},
    {"runs": [(u"· 543 项自动化测试通过（记录值 71.79 秒，0 跳过）。", 9, SUB)], "space_before": 4},
    {"runs": [(u"· 知识图谱压测：10k 节点 / 100 万边邻居查询 p95 约 63 ms；100k 节点 / 100 万边 p95 约 47 ms。", 9, SUB)],
     "space_before": 2},
    {"runs": [(u"· 课堂回放：34 个视频 / 68.66 小时全部解码并走通转写链路，CPU 实时率 0.15–0.19。", 9, SUB)],
     "space_before": 2},
    {"runs": [(u"· Windows 安装包 + 自检；断网也能演示采集、证据包与找回片段。", 9, SUB)], "space_before": 2},
    {"runs": [(u"· 尚未完成：证据包查看页 / 账本表格 / 片段卡的富 UI（本次冲刺做）；不宣称法律级取证。", 9, SUB)],
     "space_before": 2},
])
note(s, u"技术页 40 秒：不要背架构。说三件事——三源采集、带时间的记忆、证据三技能已在后端跑通；"
        u"再补一句“尚未完成的部分我们不装作做完了”。")

# ---------------------------------------------------------------- 10 business model
s = S[9]
title(s, u"商业模式：AI PC 上的“可信办公”基础层")
drop_named(s, "文本框 1")
drop_named(s, "Shape 147")
drop_named(s, "Shape 149")
layers = [
    (u"个人版", u"本地基础功能免费 / 低价买断", u"获客与口碑"),
    (u"Pro", u"跨会议项目记忆、知识图谱、证据包导出（订阅）", u"主收入"),
    (u"Team", u"共享项目记忆、权限、审计、合规导出（席位费）", u"企业续费"),
    (u"OEM", u"预装进联想 AI PC / Magic Bay 生态", u"渠道放量"),
]
for i, (t, b, tag) in enumerate(layers):
    sp = slide = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                   Inches(0.42), Inches(1.42 + i * 0.92), Inches(5.5), Inches(0.80))
    sp.adjustments[0] = 0.06
    sp.fill.solid(); sp.fill.fore_color.rgb = RGBColor.from_string(CARD_FILL)
    sp.line.color.rgb = RGBColor.from_string(CARD_LINE); sp.line.width = Pt(0.75)
    sp.shadow.inherit = False
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Pt(12); tf.margin_right = Pt(8)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    r = p.add_run(); r.text = t + u"　"; style_run(r, 11.5, TEAL, True)
    r = p.add_run(); r.text = b; style_run(r, 9.5, INK)
    r = p.add_run(); r.text = u"　[" + tag + u"]"; style_run(r, 8.5, SUB)
textbox(s, 6.15, 1.42, 3.45, 4.0, [
    {"runs": [(u"一句话：", 10.5, INK, True), (u"AI PC 里的“账本”", 10.5, TEAL, True)]},
    {"runs": [(u"财务软件管钱，meetingVAR 管承诺——每个办公室都该有一层本地、可校验的会议证据。", 9.5, INK)],
     "space_before": 4},
    {"runs": [(u"获客", 10, TEAL, True)], "space_before": 14},
    {"runs": [(u"课题组与学生自用 → 口碑传播 → 联想 AI PC 预装。", 9.5, SUB)], "space_before": 3},
    {"runs": [(u"持续开发", 10, TEAL, True)], "space_before": 10},
    {"runs": [(u"个人版养 Pro，Team / OEM 养行业化（律所、咨询、科研）。", 9.5, SUB)], "space_before": 3},
    {"runs": [(u"以上为规划，尚无收费用户；先做可演示的免费版。", 8.5, SUB, False, True)], "space_before": 12},
])
note(s, u"商业页 25 秒：四层只用一句话带过，重点说“为什么联想愿意预装”——"
        u"它是 AI PC 上唯一能把本地算力变成“可证明”的软件能力。")

# ---------------------------------------------------------------- 11 validation
s = S[10]
title(s, u"验证结果与计划 · 成功参数 · 风险")
drop_named(s, "文本框 1")
shp = find(s, "Text Placeholder 1")
shp.left, shp.top = Inches(0.42), Inches(1.34)
shp.width, shp.height = Inches(4.45), Inches(3.9)
set_text(shp.text_frame, [
    {"runs": [(u"已完成（真实可演示）", 13, TEAL, True)]},
    {"runs": [(u"· 端到端可用：采集 → 命中 → 证据包 / 账本 / 找回 30 秒。", 10.5, INK)], "space_before": 7},
    {"runs": [(u"· 543 项自动化测试通过（记录值 71.79 秒）。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"· 68.66 小时课堂回放、知识图谱百万边压测。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"· Windows 安装包 + 自检，断网可演示。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"接下来 4 周", 13, TEAL, True)], "space_before": 16},
    {"runs": [(u"· 课题组 10 人试用：看“找回原话”会不会变成周习惯。", 10.5, INK)], "space_before": 7},
    {"runs": [(u"· 本地链路连续 3 次在 5 分钟内完成彩排。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"· 录前同意勾选写入 manifest，替换写死的 consent 字段。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"· 补齐证据包查看页 / 账本表格 / 片段卡的界面。", 10.5, INK)], "space_before": 4},
])
shp = find(s, "Text Placeholder 2")
shp.left, shp.top = Inches(5.12), Inches(1.34)
shp.width, shp.height = Inches(4.45), Inches(3.9)
set_text(shp.text_frame, [
    {"runs": [(u"成功参数", 13, TEAL, True)]},
    {"runs": [(u"· 证据包 SHA256 全部校验通过；找回片段可播放。", 10.5, INK)], "space_before": 7},
    {"runs": [(u"· 试用者主动回访频次（周留存）。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"· 不编造识别准确率：没有人工逐字稿就不报 CER/WER。", 10.5, SUB, False, True)], "space_before": 4},
    {"runs": [(u"主要风险与对策", 13, TEAL, True)], "space_before": 16},
    {"runs": [(u"· 噪声环境 ASR / 触发不稳 → 手动 VAR 兜底。", 10.5, INK)], "space_before": 7},
    {"runs": [(u"· 隐私顾虑 → 选择性取证 + 录前确认。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"· 大厂入场 → 证据层 + 本地优先作差异化壁垒。", 10.5, INK)], "space_before": 4},
    {"runs": [(u"· 现场设备不确定 → 预录 fallback + 任意副屏演示。", 10.5, INK)], "space_before": 4},
])
note(s, u"验证页 30 秒：左边是“已经能演的”，右边是“还没做到的和怎么防”。"
        u"评委最反感的是把计划说成成绩，所以两边分别写清。")

# ---------------------------------------------------------------- 12 SDG metrics
s = S[11]
title(s, u"关键 SDG 成功指标", "Key SDG success metric(s) · SDG 16")
drop_named(s, "文本框 1")
drop_named(s, "文本占位符 2")
lead(s, u"对齐目标 16.6 / 16.7：建设有效、问责、透明的机构。")
card(s, 0.42, 1.80, 4.5, 1.85, u"定量（试点期测）", [
    u"· 已保存证据的 SHA256 校验通过率 = 100%；",
    u"· 证据包可离线打开并复算哈希 = 是；",
    u"· 找回原话耗时：从“翻 2 小时录像”到秒级定位；",
    u"· 可追踪承诺数、改口 diff 检出正确率（人工核对）；",
    u"· 零命中会议落盘片段数 = 0（隐私的可测承诺）；",
    u"· 断网条件下核心链路可用 = 100%。",
], 11.5, 10)
card(s, 5.08, 1.80, 4.5, 1.85, u"定性（用户访谈）", [
    u"· 会后扯皮 / 重复确认是否减少；",
    u"· “原话在不在”带来的信任感与安全感；",
    u"· 责任是否更清晰：谁承诺、谁改口可追溯；",
    u"· 团队是否愿意把它纳入常规会议流程；",
    u"· 声明：无人工逐字稿不报 CER/WER，",
    u"　无人工标注不报实体 / 关系 P/R。",
], 11.5, 10, anchor=MSO_ANCHOR.MIDDLE)
textbox(s, 0.42, 3.95, 9.15, 0.85, [
    {"runs": [(u"为什么这是 SDG 16 而不是“效率工具”：", 10, INK, True),
              (u"目标 16.6 要求机构“有效、问责、透明”。", 10, INK)]},
    {"runs": [(u"会议是组织做决定的现场，但绝大多数决定只留下转述、没有可核验的记录——"
               u"这层基础设施补的就是“问责的可执行性”。", 9, SUB)], "space_before": 3},
])
note(s, u"SDG 页 25 秒：把“可核验”直接映射到 16.6 的问责与透明，不要说成泛泛的“提升效率”。"
        u"同时当场说明我们不报假指标。")

# ---------------------------------------------------------------- 13 threats
s = S[12]
title(s, u"对 SDG 创新的威胁 Threats")
drop_named(s, "文本框 1")
drop_named(s, "文本占位符 2")
risks = [
    (u"意外后果：监控感 / 隐私侵蚀", [
        u"对策：不命中不落盘；只存片段不存全场；",
        u"录前确认 + 同意状态写入 manifest（可导出）；",
        u"数据默认不出本机，本地即可完整使用。"]),
    (u"被误读为“法律级取证”", [
        u"SHA256 只证明文件完整性，不是司法鉴定；",
        u"话术固定为“可校验的完整性”，不过度承诺。"]),
    (u"影响难以正面度量", [
        u"不编造指标：没有人工逐字稿不报 CER/WER，",
        u"没有真人评测不报满意度；只用可复现的工程基线。"]),
    (u"对平台 / 赛道的依赖", [
        u"Magic Bay 未真机验证 → 只说“标准设备 + ready”；",
        u"主机私有协议不碰、不逆向，拿到 SDK 再加适配。"]),
]
bottom = card_grid(s, 1.40, 0.42, 4.5, 0.16, risks, 11.5, 10, per_row=2, row_gap=0.22, fill_to=4.55)
textbox(s, 0.42, bottom + 0.34, 9.15, 0.4, [
    {"runs": [(u"判断标准：如果一项功能说不清“谁受影响、怎么退出”，就默认不做。", 9.5, TEAL, True)]},
])
note(s, u"风险页 25 秒：主动说监控感和“法律级”两个最容易被评委挑战的点，"
        u"并给出可执行的产品约束，而不是口头保证。")

# ---------------------------------------------------------------- 14 closing
s = S[13]
shp = find(s, "文本框 2")
shp.left, shp.top = Inches(0.55), Inches(2.05)
shp.width, shp.height = Inches(4.25), Inches(1.95)
set_text(shp.text_frame, [
    {"runs": [(u"期待与您合作", 24, INK, True)]},
    {"runs": [(u"别人帮你记下来，", 12.5, TEAL, True)], "space_before": 10},
    {"runs": [(u"meetingVAR 帮你证出来。", 12.5, TEAL, True)]},
    {"runs": [(u"AI PC 上的个人证据层，欢迎联想团队与课题组一起把它磨锋利。", 9, SUB)], "space_before": 7},
])
note(s, u"收尾 15 秒：回到那句 slogan，然后直接邀请合作——"
        u"我们要的是真机与真实场景，不是一个奖。")

prs.save(OUT)
print("saved", OUT)