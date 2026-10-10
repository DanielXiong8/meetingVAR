"""meetingVAR 路演 PPT 生成器（12 页，16:9）。

设计：深蓝底（#0B1D3A）+ 白字 + 品牌橙（#FF6B35）强调——科技感、对比强、
投影仪友好。每页大标题 + 3-5 条要点，路演 10 分钟节奏。
"""
from __future__ import annotations

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Cm, Emu, Pt

BG = RGBColor(0x0B, 0x1D, 0x3A)      # 深蓝底
ACCENT = RGBColor(0xFF, 0x6B, 0x35)  # 品牌橙
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREY = RGBColor(0xB8, 0xC4, 0xD9)    # 次级文字
CARD = RGBColor(0x12, 0x2B, 0x52)    # 卡片底

W, H = Cm(33.87), Cm(19.05)  # 16:9

prs = Presentation()
prs.slide_width, prs.slide_height = W, H
BLANK = prs.slide_layouts[6]


def add_slide():
    s = prs.slides.add_slide(BLANK)
    bg = s.background.fill
    bg.solid()
    bg.fore_color.rgb = BG
    return s


def textbox(s, l, t, w, h, text, size, color=WHITE, bold=False, align=PP_ALIGN.LEFT, font="Microsoft YaHei"):
    box = s.shapes.add_textbox(l, t, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    lines = text.split("\n")
    for i, line in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.text = line
        para.alignment = align
        para.font.size = Pt(size)
        para.font.bold = bold
        para.font.color.rgb = color
        para.font.name = font
    return box


def bullets(s, l, t, w, items, size=18, color=WHITE, gap=6):
    box = s.shapes.add_textbox(l, t, w, Cm(1.2) * len(items) + Cm(1))
    tf = box.text_frame
    tf.word_wrap = True
    for i, it in enumerate(items):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.text = "▸ " + it
        para.font.size = Pt(size)
        para.font.color.rgb = color
        para.font.name = "Microsoft YaHei"
        para.space_after = Pt(gap)
    return box


def card(s, l, t, w, h, title, body, tsize=16, bsize=13):
    from pptx.enum.shapes import MSO_SHAPE

    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, l, t, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = CARD
    sh.line.color.rgb = ACCENT
    sh.line.width = Pt(0.75)
    tf = sh.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Cm(0.4)
    p1 = tf.paragraphs[0]
    p1.text = title
    p1.font.size = Pt(tsize)
    p1.font.bold = True
    p1.font.color.rgb = ACCENT
    p1.font.name = "Microsoft YaHei"
    p2 = tf.add_paragraph()
    p2.text = body
    p2.font.size = Pt(bsize)
    p2.font.color.rgb = WHITE
    p2.font.name = "Microsoft YaHei"
    return sh


# ============ P1 封面 ============
s = add_slide()
textbox(s, Cm(2), Cm(5.2), W - Cm(4), Cm(3.5), "meetingVAR", 66, WHITE, bold=True)
textbox(s, Cm(2), Cm(8.8), W - Cm(4), Cm(2), "像足球 VAR 一样工作的会议 AI", 30, ACCENT, bold=True)
textbox(s, Cm(2), Cm(12.2), W - Cm(4), Cm(3),
        "不是记下一切，是保住对你重要的每一刻。\nSDG Open Hack 2026 · Hack16 · AI 办公任务助手", 18, GREY)

# ============ P2 问题 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "通用 AI 纪要的结构性缺陷", 36, WHITE, bold=True)
textbox(s, Cm(2), Cm(2.9), W - Cm(4), Cm(1), "所有云端纪要都是全场统一总结——同一条平滑文档发给每一个人", 16, GREY)
card(s, Cm(2), Cm(4.4), Cm(9.5), Cm(5.2), "组会 · 漏信息",
     "师兄汇报与你无关，你选择性听讲；\nPI 突然说「这篇论文对你有影响」\n——通用纪要一笔带过，你错过了。")
card(s, Cm(12.2), Cm(4.4), Cm(9.5), Cm(5.2), "例会 · 听不懂",
     "项目对 AI 是一张白纸：\n专业术语转写错误、不知所云；\n上司抛出的问题细节没记清。")
card(s, Cm(22.4), Cm(4.4), Cm(9.5), Cm(5.2), "培训 · 密度低",
     "纪要无侧重；\n2 小时回放找 3 分钟重点；\n主办方可能不开回放。")
textbox(s, Cm(2), Cm(10.4), W - Cm(4), Cm(2.2),
        "根因：「记什么」这个决定，不该由一个通用模型替所有用户做。\n"
        "云端纪要的数据出域，让法务 / 医疗 / 金融 / 研发直接出局。", 20, WHITE, bold=True)
textbox(s, Cm(2), Cm(13.6), W - Cm(4), Cm(1.2), "市场：AI 会议助手 $3.91B (2026) → $9.33B (2030)，TBRC；宽口径上探 $21B", 15, ACCENT)

# ============ P3 方案总览 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "meetingVAR：会议取证器", 36, WHITE, bold=True)
textbox(s, Cm(2), Cm(2.9), W - Cm(4), Cm(1), "系统级采集 · 选择性取证 · 端侧离线——三大优势一个组合，四家头部都没有", 16, GREY)
steps = [
    ("会前 · 记忆层", "AI 自动研读你的项目文件夹：\n代码符号 / 文档术语 / 聊天记录语义\n→ 生成监听词表 + 人物角色名单\n（用户不给一个词，AI 自己读）"),
    ("会中 · 取证层", "系统级音频采集（平台无关）\n10 分钟环形缓冲，默认零落盘\n三层监听命中 → 回溯保存前 300 秒\n语音 + 屏幕 + 30fps 高清补录"),
    ("会后 · 报告层", "薄纪要：全场速览 + 待办@人名\n厚纪要 VAR 报告：七段结构\n每条结论带时间戳——\n可回听录音 / 翻屏幕帧 / 播高清录像"),
]
for i, (t, b) in enumerate(steps):
    card(s, Cm(2) + i * Cm(10.2), Cm(4.6), Cm(9.4), Cm(8.2), t, b, tsize=20, bsize=14)
textbox(s, Cm(2), Cm(13.6), W - Cm(4), Cm(1.5),
        "像 VAR：场上不吹哨，判罚时刻才调回放——回看的永远是「已经发生的回合」，命中前的铺垫一个字不丢。", 17, ACCENT, bold=True)

# ============ P4 技术与创新 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "技术与创新点", 36, WHITE, bold=True)
bullets(s, Cm(2.5), Cm(3.4), W - Cm(5), [
    "全链路端侧：识别 / 说话人分离 / 术语纠错 / 语义大模型（Qwen3-4B int4）全部本机——数据不出机，一句话通过全部隐私审查",
    "选择性取证：1 小时会议只存 3–5 个片段 ≈ 20MB（全量录屏 10–60GB），信息密度提升两个数量级",
    "三级漏斗：代码符号提取（秒级零 token）→ 文档小模型 → 仓库语义只喂 KB 级摘要——任意项目规模不爆 context",
    "术语自适应：项目记忆驱动的转写纠错（「测试污染」→「测序污染」），组内黑话越用越准",
    "诚实红线：报告强制区分「录音明确说的」与「推断」，不编造——取证器的可信度基础",
], size=19, gap=14)

# ============ P5 实测进展 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "当前进展：MVP 已跑通（立项 7 天）", 36, WHITE, bold=True)
textbox(s, Cm(2), Cm(2.9), W - Cm(4), Cm(1), "53+ 提交 · 26 个 PR 全合并 · CI 全绿 · 离线断网验证通过", 16, ACCENT, bold=True)
for i, (t, b) in enumerate([
    ("会前", "三级漏斗词表自动生成（63 符号词 / 4 秒）；本地 Qwen3-4B 抽出人物名单带角色（10.6 秒）"),
    ("会中", "系统音频环回 + 8 秒滑窗转写（说话人+标点）+ 术语纠错 v4 + 三层监听 + 回溯取证全链路"),
    ("会后", "薄纪要 / 厚纪要 VAR 报告（关键决策 / 金句时刻 / 待办@人名）；项目记忆跨会话演化"),
    ("工程", "model-free CI、契约测试 6/6、23 条踩坑记录、完整计划书 + 市场调研 + BP 入库"),
]):
    card(s, Cm(2) + (i % 2) * Cm(15.2), Cm(4.4) + (i // 2) * Cm(4.6), Cm(14.6), Cm(4), t, b, tsize=18, bsize=13)
textbox(s, Cm(2), Cm(14.2), W - Cm(4), Cm(1.2), "开源工程实践：分支 + PR 全流程，队友贡献经 Review 合并——「会用 AI 干活」的实证", 14, GREY)

# ============ P6 竞争格局 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "竞争格局：结构空位，不是功能差距", 36, WHITE, bold=True)
headers = ["阵营", "代表", "天花板", "meetingVAR"]
rows = [
    ("全量记录派", "Otter / Fireflies / Limitless(Meta)", "存储隐私负担在用户；云端出域", "默认零落盘，只存命中时刻"),
    ("平台内置派", "腾讯元宝 / 飞书妙记 / 钉钉", "跨平台不可能；生态锁定", "系统级采集，平台无关"),
    ("关键词标记派", "Fireflies Topic Trackers", "事后贴标签；命中前语境丢失", "事中多模态取证 + 记忆触发"),
    ("总结层创新", "飞书智能纪要", "总结再好，采集与选择没做", "总结层照单全收，采集层不让"),
]
tbl = s.shapes.add_table(5, 4, Cm(2), Cm(3.4), W - Cm(4), Cm(10)).table
for j, h in enumerate(headers):
    cell = tbl.cell(0, j)
    cell.text = h
    cell.text_frame.paragraphs[0].font.bold = True
    cell.text_frame.paragraphs[0].font.size = Pt(16)
    cell.text_frame.paragraphs[0].font.color.rgb = ACCENT
for i, row in enumerate(rows, 1):
    for j, v in enumerate(row):
        cell = tbl.cell(i, j)
        cell.text = v
        cell.text_frame.paragraphs[0].font.size = Pt(13)
        cell.text_frame.paragraphs[0].font.color.rgb = WHITE
textbox(s, Cm(2), Cm(14.2), W - Cm(4), Cm(1.5), "四家都没有做「系统级采集 + 选择性取证 + 端侧离线」的组合——蓝海。", 18, ACCENT, bold=True)

# ============ P7 目标用户 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "目标用户与进入路径", 36, WHITE, bold=True)
card(s, Cm(2), Cm(3.6), Cm(14.6), Cm(4.6), "首发 · 研究生与科研团队",
     "组会 / 文献讨论：术语尖锐、选择性听讲普遍、隐私敏感；\n我们自己就是用户——演示主线即真实需求。校园冷启动（edu 免费）。")
card(s, Cm(17.4), Cm(3.6), Cm(14.4), Cm(4.6), "扩展 · 工程师 / 咨询法务 / 销售",
     "项目例会与评审、合同条款初筛（隐私刚需最强）、客户会议取证。\n场景族：组会 / 例会 / 评审 / 培训共用同一核心问题，产品一套通吃。")
textbox(s, Cm(2), Cm(9.2), W - Cm(4), Cm(2), "商业模式", 26, ACCENT, bold=True)
bullets(s, Cm(2.5), Cm(11), W - Cm(5), [
    "免费档：核心取证（纯 CPU 可跑）——获客钩子    专业版 ¥39/月：本地语义大模型全开 + 高清补录 + 记忆演化",
    "团队版席位制：项目记忆库共享 + 任务分派    企业版私有化：数据不出机 = 合规刚需的解药（法务/医疗/金融）",
    "联想协同：AI PC 端侧预装（全本地架构 = AI PC 算力最佳展示位）；远期 Magic Bay 外接取证模块",
], size=16, gap=10)
textbox(s, Cm(2), Cm(15.2), W - Cm(4), Cm(1.5), "端侧架构的第二个商业红利：带宽账单是零 → 毛利率 >85%", 17, ACCENT, bold=True)

# ============ P8 使用前后 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "使用前 vs 使用后", 36, WHITE, bold=True)
tbl = s.shapes.add_table(6, 3, Cm(2), Cm(3.4), W - Cm(4), Cm(11)).table
for j, h in enumerate(["维度", "现状", "用 meetingVAR"]):
    cell = tbl.cell(0, j)
    cell.text = h
    cell.text_frame.paragraphs[0].font.bold = True
    cell.text_frame.paragraphs[0].font.size = Pt(16)
    cell.text_frame.paragraphs[0].font.color.rgb = ACCENT
for i, row in enumerate([
    ("关键信息", "通用纪要一笔带过或丢失", "命中片段 100% 留证（含前 5 分钟铺垫）"),
    ("回看成本", "2 小时回放找 3 分钟重点", "每片段即「值得看的全部」+ 高清现场"),
    ("术语准确", "白纸 AI 转写错误", "项目记忆纠错，越用越准"),
    ("隐私", "云端数据出域", "全端侧，数据不出机"),
    ("存储", "全量录音 1h ≈ GB 级", "一场会议 ≈ 20MB"),
], 1):
    for j, v in enumerate(row):
        cell = tbl.cell(i, j)
        cell.text = v
        cell.text_frame.paragraphs[0].font.size = Pt(14)
        cell.text_frame.paragraphs[0].font.color.rgb = WHITE
textbox(s, Cm(2), Cm(15), W - Cm(4), Cm(1.2), "触发精确率 / 召回率、字错率：剧本验证协议就绪，数字实测后填入（不拍脑袋）", 14, GREY)

# ============ P9 路线图 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "路线图", 36, WHITE, bold=True)
for i, (t, b) in enumerate([
    ("现在", "MVP 全链路端侧跑通；离线验证绿；BP / 市场调研 / 计划书齐备"),
    ("1 个月", "剧本级 P/R 验证数字；字级流式转写；HTML 报告时间戳回跳；场景模板 ×2"),
    ("3 个月", "出口分级过滤（防过度触发）；打包分发；10 个种子用户实测迭代"),
    ("6–12 个月", "团队版；企业私有化试点；AI PC 生态洽谈"),
]):
    card(s, Cm(2) + i * Cm(7.75), Cm(4.2), Cm(7.3), Cm(6.5), t, b, tsize=22, bsize=14)

# ============ P10 团队 ============
s = add_slide()
textbox(s, Cm(2), Cm(1.2), W - Cm(4), Cm(1.6), "团队", 36, WHITE, bold=True)
card(s, Cm(2), Cm(3.6), Cm(14.6), Cm(5.5), "清华跨学科学生团队",
     "工程线：音频管线 / 模型部署 / CI（53+ 提交可用系统）\n产品智能线：LLM 工程 / 报告系统 / 竞品研究\n顾问：赛事学术导师（清华计算机系）+ 企业导师（联想创新加速器）")
card(s, Cm(17.4), Cm(3.6), Cm(14.4), Cm(5.5), "融资计划（示意）",
     "种子：比赛奖金池（一等 ¥20,000）\n天使轮目标 ¥200–300 万：60% 研发 / 25% 市场 / 15% 运营\n专业版 ¥39/月 × 1 万用户 = 年流水 ≈ ¥470 万，毛利率 >85%")
textbox(s, Cm(2), Cm(10.4), W - Cm(4), Cm(2.5),
        "全程 AI Agent 协作开发——\n「会用 AI 干活」正是这个赛道要验证的能力，我们就是示范。", 22, ACCENT, bold=True)
textbox(s, Cm(2), Cm(13.8), W - Cm(4), Cm(1.5), "开源工程实践：分支 + PR 全流程 · CI 自动化 · 23 条踩坑记录 · 完整文档体系", 15, GREY)

# ============ P11 收尾 ============
s = add_slide()
textbox(s, Cm(2), Cm(5), W - Cm(4), Cm(3), "不是记下一切，", 44, WHITE, bold=True)
textbox(s, Cm(2), Cm(7.5), W - Cm(4), Cm(3), "是保住对你重要的每一刻。", 44, ACCENT, bold=True)
textbox(s, Cm(2), Cm(12), W - Cm(4), Cm(2), "meetingVAR · record your meeting like VAR referee", 20, GREY)

OUT = r"C:\Users\Lenovo\OneDrive\Desktop\联想未来办公AI创新挑战\meetingVAR_路演.pptx"
prs.save(OUT)
print("saved:", OUT, "| slides:", len(prs.slides.__iter__.__self__._sldIdLst))
