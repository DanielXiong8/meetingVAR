"""L3 事件裁判（离线/影子）：输出契约 v0 + 滑窗判定 + 结构化校验 + 证据按 turn_ids 回填。

依据（GPT 第四轮裁决，2026-10-17）：
- 输出契约：event_type（7 类）/ event_status（8 态）/ focus_id / evidence_turn_ids /
  reason / confidence / owner / deadline / related_event_id；
- 证据纪律（本项目比裁决更严）：**模型只给 turn_ids，引文由系统从原文逐字回填**——
  4B 冒烟实测其复述引文不可信；
- 位置盲：喂给模型的窗口不含时钟/绝对时间（turn 序号 + 文本），供离线评测与影子模式；
- 窗口 120s / 步长 30s 是**离线实验起点**，不是已验证最优（test3 用的就是这组）。

关键约束：本模块只做"生成预测"，不改变真实保存链路（纪律：评测通过前不改触发）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

EVENT_TYPES = frozenset({
    "DECISION", "COMMITMENT", "DIRECTION", "RISK_BLOCKER",
    "OPEN_QUESTION", "DISAGREEMENT", "EVIDENCE_UPDATE",
})
EVENT_STATUSES = frozenset({
    "proposed", "discussed", "decided", "assigned",
    "rejected", "deferred", "unresolved", "revised",
})

_LINE_RE = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*([^:]{1,40}?):\s*(.*)$")


def parse_transcript(path: Path) -> list[dict]:
    """转写 txt → turns（id/turn_id/t0/t1/speaker/text）。

    支持 `[HH:MM]` 与 `[HH:MM:SS]`；时钟回退超过 12 小时按跨午夜处理；
    t0=相对首句秒数；t1=下一句 t0（同刻/乱序时 +1s），末句 +2s（≤60s 封顶）。
    turn 序号（0 起）与 benchmark_v0 的 evidence_turn_ids 对齐。
    """
    rows: list[dict] = []
    last_clock = None
    day_offset = 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        h, mi = int(m.group(1)), int(m.group(2))
        s = int(m.group(3) or 0)
        raw = h * 3600 + mi * 60 + s
        if last_clock is not None and raw + day_offset < last_clock - 12 * 3600:
            day_offset += 86400  # 跨午夜：时钟回绕超过 12 小时视为次日
        cur = raw + day_offset
        last_clock = cur
        rows.append({"id": len(rows), "turn_id": f"turn_{len(rows)}",
                     "clock": cur, "speaker": m.group(4).strip(),
                     "text": m.group(5).strip()})
    if not rows:
        return []
    t_first = rows[0]["clock"]
    for r in rows:  # 先算全部 t0（t1 依赖下一句的 t0，必须两遍）
        r["t0"] = float(r["clock"] - t_first)
    for i, r in enumerate(rows):
        if i + 1 < len(rows):
            nxt = rows[i + 1]["t0"]
            r["t1"] = nxt if nxt > r["t0"] else r["t0"] + 1.0
        else:
            r["t1"] = r["t0"] + 2.0
        r["t1"] = min(r["t1"], r["t0"] + 60.0)
    return rows


def windows(turns: list[dict], window: float = 120.0, stride: float = 30.0) -> list[dict]:
    """按时间切窗口：[ws, we) 内的 turn 组成判定单元（窗口自带序号，无时钟）。"""
    if not turns:
        return []
    total = turns[-1]["t1"]
    out = []
    ws = 0.0
    idx = 0
    while ws < total:
        we = ws + window
        sel = [t for t in turns if ws <= t["t0"] < we]
        if sel:
            out.append({"idx": idx, "start": ws, "end": we, "turns": sel})
            idx += 1
        ws += stride
    return out


def load_focus(path: Path | None) -> list[dict]:
    """关注清单：支持 {"foci":[...]}（benchmark_v0）或纯文本行（每行一个目标）。"""
    if path is None:
        return []
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except ValueError:
        return [{"focus_id": f"focus_{i + 1}", "goal": ln.strip()}
                for i, ln in enumerate(raw.splitlines()) if ln.strip()]
    if isinstance(data, dict) and isinstance(data.get("foci"), list):
        return data["foci"]
    if isinstance(data, list):
        return data
    return []


def load_labels(path: Path | None) -> list[dict]:
    """标注：支持 {"events":[...]}（benchmark_v0）或 [{"start","end",...}]。"""
    if path is None:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("events") if isinstance(data, dict) else data
    out = []
    for i, e in enumerate(items or []):
        if "start" in e:  # 旧格式
            start, end = float(e["start"]), float(e["end"])
        else:  # benchmark_v0：start_time/end_time
            start, end = float(e["start_time"]), float(e["end_time"])
        out.append({
            "event_id": e.get("event_id", f"evt_{i + 1:03d}"),
            "start": start, "end": end,
            "event_type": e.get("event_type"),
            "event_status": e.get("event_status"),
            "focus_ids": e.get("focus_ids") or [],
            "evidence_turn_ids": [int(x) for x in (e.get("evidence_turn_ids") or [])],
        })
    return out


_SYS = """你是会议事件裁判。下面是一段会议转写（每条一个轮次，含编号 turn_N、说话人和文本）。
请判断这段里是否出现"对项目有实际影响、值得留存证据"的事件。

只输出一个 JSON：
{"hit": true/false,
 "event_type": "DECISION|COMMITMENT|DIRECTION|RISK_BLOCKER|OPEN_QUESTION|DISAGREEMENT|EVIDENCE_UPDATE",
 "event_status": "proposed|discussed|decided|assigned|rejected|deferred|unresolved|revised",
 "focus_id": "对应关注点编号或 null",
 "evidence_turn_ids": ["turn_12", "turn_13"],
 "reason": "为什么这是重要事件（一句话）",
 "confidence": 0.0-1.0 或 null,
 "owner": "责任人（若明确）或 null",
 "deadline": "截止时间（若明确）或 null",
 "related_event_id": null}
硬规则：
1) 只依据给定文本；evidence_turn_ids 必须来自本窗口的编号，且**只填真正支撑判断的轮次**；
2) 不要复述或改写原文作为证据（系统会按编号回填原句）；
3) 只是话题重复、状态汇报、闲聊 → hit=false；
4) 讨论中的想法不能标成 decided/assigned；没有明确承诺不要标 COMMITMENT；
5) reason 不超过 30 字；未确定的字段直接省略（不要输出 null）。
<<FOCUS>>
【本窗口转写】
<<TURNS>>
/no_think
"""


def _focus_block(focus: list[dict]) -> str:
    if not focus:
        return "【关注点】无（按通用事件模式判断）\n"
    lines = ["【关注点（只有满足“应触发”才算命中，单次提及不算）】"]
    for f in focus:
        lines.append(f"- {f.get('focus_id', '?')}: {f.get('goal', '')}")
        if f.get("should_trigger"):
            lines.append(f"  应触发：{f['should_trigger']}")
        if f.get("should_not_trigger_alone"):
            lines.append(f"  仅此不能触发：{f['should_not_trigger_alone']}")
    return "\n".join(lines) + "\n"


def _turns_block(turns: list[dict]) -> str:
    return "\n".join(f"{t['turn_id']} [{t['speaker']}] {t['text']}" for t in turns)


def extract_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    cleaned = re.sub(r"```(?:json)?|```", "", raw)
    m = re.search(r"\{.*\}", cleaned, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def judge_window(turns: list[dict], focus: list[dict], chat_fn) -> dict | None:
    """判定一个窗口；非法/无证据 → 返回 hit=False 的结果（不抛异常，便于批量跑）。"""
    if not turns:
        return None
    prompt = _SYS.replace("<<FOCUS>>", _focus_block(focus)).replace("<<TURNS>>", _turns_block(turns))
    data = extract_json(chat_fn(prompt, temperature=0.2, timeout=300))
    if data is None:
        return None
    by_id = {t["turn_id"]: t for t in turns}
    hit = bool(data.get("hit"))
    etype = str(data.get("event_type") or "").strip().upper()
    status = str(data.get("event_status") or "").strip().lower()
    ids_raw = data.get("evidence_turn_ids") or []
    ids = []
    for x in ids_raw if isinstance(ids_raw, list) else []:
        tid = str(x).strip()
        if tid in by_id:
            ids.append(by_id[tid]["id"])
    if hit and etype not in EVENT_TYPES:
        return {"hit": False, "reason": f"非法 event_type={etype!r}（已判否）"}
    if hit and not ids:
        return {"hit": False, "reason": "证据轮次为空或不在窗口内（宁漏勿误，判否）"}
    evidence_ids = []
    if hit:
        evidence_ids = sorted(set(ids))
    quote = " ".join(t["text"] for t in turns if t["id"] in evidence_ids).strip()
    conf = data.get("confidence")
    try:
        conf = max(0.0, min(1.0, float(conf))) if conf is not None else None
    except (TypeError, ValueError):
        conf = None
    focus_id = str(data.get("focus_id") or "").strip() or None
    valid_focus = {f.get("focus_id") for f in focus}
    if focus_id and valid_focus and focus_id not in valid_focus:
        focus_id = None
    return {
        "hit": hit,
        "event_type": etype if hit else None,
        "event_status": status if (hit and status in EVENT_STATUSES) else ("discussed" if hit else None),
        "focus_id": focus_id if hit else None,
        "evidence_turn_ids": evidence_ids,
        "evidence_quote": quote,
        "reason": str(data.get("reason") or "")[:200],
        "confidence": conf,
        "owner": (str(data.get("owner")).strip() or None) if data.get("owner") else None,
        "deadline": (str(data.get("deadline")).strip() or None) if data.get("deadline") else None,
        "related_event_id": data.get("related_event_id"),
    }


def merge_hits(hits: list[dict], gap: float = 60.0) -> list[dict]:
    """相邻同类型命中合并为一个事件（按时间区间+类型身份去重，GPT 裁决要求）。

    v0 规则（设计参数，待校准）：同一 event_type + focus_id，且后一窗口起点
    ≤ 前一事件终点 + gap → 合并（证据并集、引文拼接、区间取并）。
    """
    merged: list[dict] = []
    for h in sorted((x for x in hits if x.get("hit")), key=lambda x: x["start"]):
        if merged:
            last = merged[-1]
            if (h["event_type"] == last["event_type"]
                    and (h.get("focus_id") or None) == (last.get("focus_id") or None)
                    and h["start"] <= last["end"] + gap):
                last["end"] = max(last["end"], h["end"])
                last["evidence_turn_ids"] = sorted(set(last["evidence_turn_ids"]) | set(h["evidence_turn_ids"]))
                if h.get("evidence_quote"):
                    last["evidence_quote"] = (last.get("evidence_quote", "") + " " + h["evidence_quote"]).strip()
                last["windows"] = last.get("windows", 1) + 1
                continue
        merged.append({**h, "windows": 1})
    return merged


def judge_transcript(turns: list[dict], window: float = 120.0, stride: float = 30.0,
                     focus: list[dict] | None = None, chat_fn=None,
                     progress=None) -> list[dict]:
    """滑窗跑完整场；返回所有窗口的判定（hit 的带 start/end/证据；merge_hits 负责去重）。"""
    from ..llm import chat as default_chat

    chat_fn = chat_fn or default_chat
    wins = windows(turns, window, stride)
    out = []
    for w in wins:
        r = judge_window(w["turns"], focus or [], chat_fn)
        if r is None:
            if progress:
                progress(f"窗口 {w['idx'] + 1}/{len(wins)}：模型无输出（跳过）")
            continue
        out.append({"start": w["start"], "end": w["end"], "window_idx": w["idx"], **r})
        if progress:
            flag = f"HIT {r['event_type']}" if r["hit"] else "-"
            progress(f"窗口 {w['idx'] + 1}/{len(wins)}: {flag}")
    return out