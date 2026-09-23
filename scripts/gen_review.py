"""
自动生成每日复盘 MDX（scripts/gen_review.py）

设计原则：
1. 客观章节（一~四）全部由 src/data/*.json 真实数据模板渲染，一个数字都不编造；
2. 主观章节（五~六）交给大模型撰写，prompt 里锁死「只能引用数据摘要中出现的数字；
   不得给买卖点位 / 仓位 / 个股推荐；不吹不黑」；
3. 大模型不可用时，照样产出完整的数据复盘，观点章节明确写「待生成」，绝不编观点；
4. 数据不是当日交易日 / stale 时直接不生成——宁可当天没有复盘，也不拿旧数据凑一篇；
5. 已存在的当日 MDX 默认不覆盖（保住人工填写的战绩），要重刷必须显式 --force。

用法：
    python scripts/gen_review.py                 # 生成当日复盘
    python scripts/gen_review.py --force         # 覆盖已存在的当日复盘
    python scripts/gen_review.py --date 2026-09-22
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import now_cn, now_iso, read_json, setup_logging, to_float  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REVIEWS = ROOT / "src" / "content" / "reviews"

log = setup_logging("gen_review")

# 数据最多允许「滞后多少小时」仍算新鲜（复盘批次在收盘后运行，数据应是当天的）
MAX_DATA_AGE_HOURS = float(os.getenv("REVIEW_MAX_DATA_AGE_HOURS", "14"))
LLM_TIMEOUT = int(os.getenv("REVIEW_LLM_TIMEOUT", "120"))
LLM_TEMPERATURE = float(os.getenv("REVIEW_LLM_TEMPERATURE", "0.4"))

DISCLAIMER = (
    "> 数据部分由 `scripts/fetch_*.py` 抓取的真实公开数据生成（新浪财经 / 东方财富 / 乐咕乐股）；\n"
    "> **「复盘观点」与「明日预案」由 AI 基于当日数据撰写，仅供参考，不构成任何投资建议，据此操作风险自负。**"
)


# ---------------------------------------------------------------- 数据装载
def load(name: str) -> dict:
    try:
        return read_json(name) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("读取 %s 失败：%s", name, exc)
        return {}


def expected_trade_day() -> datetime.date:
    """按北京时间推算「今天该复盘哪个交易日」。未到收盘则复盘上一交易日。"""
    now = now_cn()
    d = now.date()
    if d.weekday() >= 5:  # 周末复盘最近一个周五
        d -= timedelta(days=d.weekday() - 4)
    elif now.hour < 15:  # 当天还没收盘
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d


def parse_day(value) -> datetime.date | None:
    if value is None:
        return None
    s = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def hours_since(iso: str) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now_cn().tzinfo)
    return (now_cn() - dt).total_seconds() / 3600


# ---------------------------------------------------------------- 客观章节
def fmt_pct(v) -> str:
    f = to_float(v)
    if f is None:
        return "—"
    if abs(f) < 0.005:  # 避免出现 -0.00%
        return "0.00%"
    return f"{f:+.2f}%"


def fmt_num(v, nd: int = 2, default: str = "—") -> str:
    f = to_float(v)
    if f is None:
        return default
    return f"{f:,.{nd}f}"


def section_overview(a: dict) -> str:
    indices = a.get("indices") or []
    lines = ["## 一、盘面概览", "", "| 指数 | 收盘 | 涨跌幅 | 振幅 | 成交额（亿） |", "| --- | --- | --- | --- | --- |"]
    for it in indices:
        lines.append(
            f"| {it.get('name', '—')} | {fmt_num(it.get('close'))} | {fmt_pct(it.get('changePct'))} "
            f"| {fmt_num(it.get('amplitude'))} | {fmt_num(it.get('amount'), 0)} |"
        )
    if not indices:
        lines.append("| — | — | — | — | — |（当日指数数据抓取失败，本节留空，不做估算）")

    vol = a.get("volume") or {}
    amount = to_float(vol.get("amount"))
    prev_amount = to_float(vol.get("prevAmount"))
    chg = to_float(vol.get("changePct"))
    sh = next((to_float(i.get("amount")) for i in indices if i.get("name") == "上证指数"), None)
    sz = next((to_float(i.get("amount")) for i in indices if i.get("name") == "深证成指"), None)
    lines.append("")
    if amount:
        tail = ""
        if sh and sz:
            tail = f"（上证 {sh:,.0f} 亿 + 深证 {sz:,.0f} 亿）"
        tag = "放量" if (chg or 0) > 0 else ("缩量" if (chg or 0) < 0 else "持平")
        lines.append(
            f"- 两市成交合计约 **{amount:,.0f} 亿**{tail}，较上一交易日 **{fmt_pct(chg)}**（{tag}）。"
        )
    else:
        lines.append("- 两市成交额接口当日不可用，该项留空。")

    b = a.get("breadth") or {}
    up, down, flat = b.get("up"), b.get("down"), b.get("flat")
    if up is not None and down is not None:
        ratio = f"（上涨占比 {up / max(up + down, 1) * 100:.1f}%）" if isinstance(up, int) else ""
        lines.append(
            f"- 涨跌家数：上涨 **{up}** 家 / 下跌 **{down}** 家 / 平盘 {flat if flat is not None else '—'} 家"
            f"（全市场 {b.get('total', '—')} 只）{ratio}。"
        )
    if b.get("limitUp") is not None:
        lines.append(f"- 涨停 **{b['limitUp']}** 家、跌停 **{b.get('limitDown', '—')}** 家。")

    m = a.get("margin") or {}
    if to_float(m.get("balance")) is not None:
        mchg = to_float(m.get("change"))
        sign = "+" if (mchg or 0) >= 0 else ""
        lines.append(
            f"- 融资余额 {to_float(m['balance']):,.2f} 亿，环比 **{sign}{mchg:,.2f} 亿**（上交所口径）。"
        )

    s = a.get("sentiment") or {}
    if s.get("score") is not None:
        drivers = "；".join(s.get("drivers") or [])
        lines.append(
            f"- 规则化情绪分：**{s['score']} / 100（{s.get('label', '')}）**——由涨跌家数、涨停家数、量能环比按固定公式计算。"
        )
        if drivers:
            lines.append(f"  驱动项：{drivers}")
    return "\n".join(lines)


def section_sectors(a: dict) -> str:
    sectors = a.get("sectors") or []
    up = [s for s in sectors if s.get("direction") == "up"][:6]
    down = [s for s in sectors if s.get("direction") == "down"][:6]
    lead_up = "、".join(f"{s.get('name')} {fmt_pct(s.get('changePct'))}" for s in up) or "（当日无上涨板块数据）"
    lead_down = "、".join(f"{s.get('name')} {fmt_pct(s.get('changePct'))}" for s in down) or "（当日无下跌板块数据）"
    return "\n".join(
        [
            "## 二、主线板块（新浪行业板块口径）",
            "",
            f"**领涨**：{lead_up}",
            "",
            f"**领跌**：{lead_down}",
            "",
            "（以上为当日真实涨跌幅排序。板块层面的驱动归因需结合消息面人工补充。）",
        ]
    )


def section_dragons(d: dict, a: dict) -> str:
    items = d.get("items") or []
    # 同一只票可能在多个榜单重复出现，按代码去重、保留净买额最大的一条
    best: dict[str, dict] = {}
    for it in items:
        code = str(it.get("code") or it.get("name"))
        net = to_float(it.get("netBuy")) or float("-inf")
        if code not in best or net > (to_float(best[code].get("netBuy")) or float("-inf")):
            best[code] = it
    rank = sorted(best.values(), key=lambda x: to_float(x.get("netBuy")) or float("-inf"), reverse=True)[:5]

    lines = ["## 三、资金面 · 龙虎榜（东方财富近一月统计口径）", "", "| 标的 | 收盘 | 涨跌幅 | 净买额（万） |", "| --- | --- | --- | --- |"]
    for it in rank:
        lines.append(
            f"| {it.get('name', '—')} {it.get('code', '')} | {fmt_num(it.get('close'))} "
            f"| {fmt_pct(it.get('changePct'))} | +{fmt_num(it.get('netBuy'), 0)} |"
        )
    if not rank:
        lines.append("| — | — | — | — |（当日龙虎榜接口不可用，本节留空）")

    ff = (a.get("fundFlow") or {})
    main = ff.get("main") or {}
    if not to_float(main.get("net")):
        lines.append("")
        lines.append(f"主力资金流向接口当日不可用，该项留空；北向实时数据已停止披露，本站不做高频解读。")
    return "\n".join(lines)


def section_global(g: dict, n: dict) -> str:
    lines = ["## 四、外围与消息面", ""]
    groups = g.get("groups") or []
    us = next((grp for grp in groups if "美股" in str(grp.get("market", ""))), None)
    ap = next((grp for grp in groups if "亚太" in str(grp.get("market", "")) or "港" in str(grp.get("market", ""))), None)

    if us:
        parts = []
        for it in (us.get("items") or [])[:3]:
            as_of = f"（截至 {it.get('asOf')[5:]}）" if it.get("asOf") else ""
            parts.append(f"{it.get('name')} {fmt_num(it.get('price'))}（{fmt_pct(it.get('changePct'))}）{as_of}")
        lines.append(f"- 上一交易日美股：{'、'.join(parts)}。")
    else:
        lines.append("- 美股快照当日抓取失败，本节留空。")

    if ap:
        parts = [f"{it.get('name')} {fmt_num(it.get('price'))}（{fmt_pct(it.get('changePct'))}）" for it in (ap.get("items") or [])[:3]]
        if parts:
            lines.append(f"- 亚太：{'、'.join(parts)}。")

    if g.get("note") and "注意" in str(g.get("note")):
        lines.append(f"- **数据缺口声明**：{g.get('note')}")

    items = (n.get("items") or [])[:6]
    if items:
        lines.append("- 当日盘后真实要闻（节选，影响分析由引擎自动生成，非人工判断）：")
        for it in items:
            title = str(it.get("title", "")).strip()
            if len(title) > 60:  # 截断时去掉可能残留的半截引号 / 括号
                title = title[:60].rstrip() .rstrip("“（（《")
            lines.append(
                f"  - {title}（{it.get('sector', '宏观')} / {it.get('sentiment', '中性')}）；"
            )
    else:
        lines.append("- 当日消息面抓取为空，本节留空。")

    analyzer = n.get("analyzer")
    if analyzer:
        note = "（大模型判定）" if analyzer == "llm" else "（规则词典兜底，可靠性低于大模型判定）"
        lines.append("")
        lines.append(f"> 注：本轮消息分析引擎标注为 `{analyzer}`{note}。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 大模型部分
SYSTEM_PROMPT = (
    "你是资深 A 股复盘作者，风格直接、有观点、不吹不黑，像一位做了十年交易的老手在收盘后做笔记。\n"
    "【硬约束】\n"
    "1. 只能使用「数据摘要」中出现过的数字与事实，禁止推算、禁止编造任何未给出的数字、"
    "禁止虚构消息或个股；\n"
    "2. 不得给出具体买卖点位、仓位比例、个股推荐、目标价；\n"
    "3. 禁止夸张修辞、情绪煽动和确定性预测，涉及未来一律用「若……则……」的条件句式；\n"
    "4. 输出必须是合法 JSON 对象，不要 markdown 代码块，不要任何解释文字。\n"
)

OUTPUT_SCHEMA = (
    "请严格按以下 JSON 结构输出：\n"
    "{\n"
    '  "title": "M月D日复盘：<14字以内的核心判断，必须由当日数据得出>",\n'
    '  "summary": "80~120字，包含三大指数收盘与涨跌幅、两市成交、涨跌家数中的至少三项",\n'
    '  "tags": ["3个2-4字标签，反映当日盘面特征"],\n'
    '  "viewpoint": [{"heading": "一句话判断，不超过20字", "body": "2-4句展开，必须引用数据摘要中的真实数字"}, ...共3-5条],\n'
    '  "plan": [{"scenario": "多头延续|中性震荡|情绪退潮", "trigger": "可验证的触发条件（量能/点位/涨停家数，数字必须来自摘要或明确的相对描述）", "action": "应对思路，不得出现买入/卖出某只股票"}, ...共3条],\n'
    '  "watchpoints": "三个关键观察点，用①②③分隔，一句话讲完"\n'
    "}\n"
)


def llm_chat(system: str, user: str) -> str | None:
    api_key = os.getenv("LLM_API_KEY")
    base = os.getenv("LLM_API_BASE")
    model = os.getenv("LLM_MODEL", "deepseek-chat")
    if not api_key or not base:
        log.warning("未配置 LLM_API_KEY / LLM_API_BASE，观点章节将留空")
        return None

    payload = {
        "model": model,
        "temperature": LLM_TEMPERATURE,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            log.warning("LLM HTTP %s（第 %d 次）", exc.code, attempt + 1)
            if exc.code in (400, 401, 403, 404):
                break
            time.sleep(2 ** attempt)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM 调用失败（第 %d 次）：%s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return None


def parse_llm_json(text: str) -> dict | None:
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


def build_digest(a: dict, d: dict, g: dict, n: dict, day: datetime.date) -> str:
    idx = a.get("indices") or []
    b = a.get("breadth") or {}
    v = a.get("volume") or {}
    m = a.get("margin") or {}
    s = a.get("sentiment") or {}
    sectors = a.get("sectors") or []
    up = [x for x in sectors if x.get("direction") == "up"][:6]
    down = [x for x in sectors if x.get("direction") == "down"][:6]

    dragons = sorted(
        {str(it.get("code")): it for it in (d.get("items") or [])}.values(),
        key=lambda x: to_float(x.get("netBuy")) or float("-inf"),
        reverse=True,
    )[:5]

    groups = g.get("groups") or []
    us = next((grp for grp in groups if "美股" in str(grp.get("market", ""))), None)
    news = (n.get("items") or [])[:10]

    parts = [f"交易日：{day.isoformat()}", ""]
    parts.append("【指数】" + "；".join(
        f"{i.get('name')} 收 {fmt_num(i.get('close'))}（{fmt_pct(i.get('changePct'))}），振幅 {fmt_num(i.get('amplitude'))}，成交 {fmt_num(i.get('amount'), 0)} 亿"
        for i in idx))
    parts.append(
        f"【量能】两市成交 {fmt_num(v.get('amount'), 0)} 亿，环比 {fmt_pct(v.get('changePct'))}（上一交易日 {fmt_num(v.get('prevAmount'), 0)} 亿）"
    )
    parts.append(
        f"【广度】上涨 {b.get('up')} 家 / 下跌 {b.get('down')} 家 / 平盘 {b.get('flat')} 家，全市场 {b.get('total')} 只；"
        f"涨停 {b.get('limitUp')} 家、跌停 {b.get('limitDown')} 家"
    )
    if to_float(m.get("balance")) is not None:
        parts.append(f"【两融】融资余额 {fmt_num(m.get('balance'))} 亿，环比 {fmt_num(m.get('change'))} 亿")
    parts.append(f"【情绪分】{s.get('score')} / 100（{s.get('label', '')}）")
    parts.append("【领涨板块】" + "、".join(f"{x.get('name')} {fmt_pct(x.get('changePct'))}" for x in up))
    parts.append("【领跌板块】" + "、".join(f"{x.get('name')} {fmt_pct(x.get('changePct'))}" for x in down))
    parts.append("【龙虎榜净买前列】" + "、".join(
        f"{x.get('name')}({x.get('code')}) 收 {fmt_num(x.get('close'))} {fmt_pct(x.get('changePct'))} 净买 {fmt_num(x.get('netBuy'), 0)} 万"
        for x in dragons))
    if us:
        parts.append("【外围美股】" + "、".join(
            f"{i.get('name')} {fmt_num(i.get('price'))}（{fmt_pct(i.get('changePct'))}，截至 {i.get('asOf') or '—'}）"
            for i in (us.get("items") or [])[:4]))
    if news:
        parts.append("【消息面】" + " || ".join(
            f"{i.get('title', '')[:50]}（{i.get('sector', '宏观')}/{i.get('sentiment', '中性')}）" for i in news))
    return "\n".join(parts)


# ---------------------------------------------------------------- 组装
def yaml_str(v: str) -> str:
    s = str(v).replace('"', "'").replace("\n", " ").strip()
    return s


def fallback_title(a: dict, day: datetime.date) -> str:
    """大模型不可用时的兜底标题：只用数据说话，不编判断。"""
    s = a.get("sentiment") or {}
    b = a.get("breadth") or {}
    v = to_float((a.get("volume") or {}).get("amount"))
    seg = f"{s.get('label') or '数据'}格局" if s.get("label") else "数据概览"
    if v:
        seg += f"，两市成交 {v / 10000:.2f} 万亿"
    up, down = b.get("up"), b.get("down")
    if isinstance(up, int) and isinstance(down, int):
        seg += f"，涨跌 {up} / {down} 家"
    return f"{day.month}月{day.day}日复盘：{seg}"


def fallback_summary(a: dict, day: datetime.date) -> str:
    idx = (a.get("indices") or [])[:2]
    b = a.get("breadth") or {}
    v = a.get("volume") or {}
    seg = []
    for i in idx:
        seg.append(f"{i.get('name')} {fmt_num(i.get('close'))}（{fmt_pct(i.get('changePct'))}）")
    if to_float(v.get("amount")):
        seg.append(f"两市成交 {to_float(v['amount']):,.0f} 亿（环比 {fmt_pct(v.get('changePct'))}）")
    if b.get("up") is not None:
        seg.append(f"上涨 {b.get('up')} 家 / 下跌 {b.get('down')} 家，涨停 {b.get('limitUp')} 家、跌停 {b.get('limitDown')} 家")
    return f"{day.month}月{day.day}日复盘：" + "，".join(seg) + "。"


def fallback_tags(a: dict) -> list[str]:
    sectors = [s for s in (a.get("sectors") or []) if s.get("direction") == "up"][:2]
    tags = [str(s.get("name"))[:4] for s in sectors]
    v = to_float((a.get("volume") or {}).get("changePct"))
    tags.append("放量" if (v or 0) > 0 else "缩量")
    return [t for t in tags if t][:3]


def build_mdx(day, a, d, g, n, llm: dict | None) -> tuple[str, str]:
    engine = "llm" if llm else "rules"
    head = str((llm or {}).get("title") or "").strip()
    if head:
        head = head.split("：")[-1].strip()
    if not head:
        head = fallback_title(a, day).split("：")[-1].strip()
    title = f"{day.month}月{day.day}日复盘：{head}"
    summary = (llm or {}).get("summary") or fallback_summary(a, day)
    tags = (llm or {}).get("tags") or fallback_tags(a)
    tags = [str(t)[:6] for t in tags][:3]
    updated = a.get("updatedAt") or now_iso()

    fm = [
        "---",
        f'title: "{yaml_str(title)}"',
        f"date: {day.isoformat()}",
        "author: AI 复盘助手（数据+观点自动生成）",
        f"batch: {a.get('batch') or 'A股收盘'}",
        f'dataUpdatedAt: "{str(updated)[:16].replace("T", " ")}"',
        "sample: false",
        f'summary: "{yaml_str(summary)}"',
        "tags: [" + ", ".join(f'"{yaml_str(t)}"' for t in tags) + "]",
        "generated: true",
        f"engine: {engine}",
        "---",
        "",
        DISCLAIMER,
        "",
        section_overview(a),
        "",
        section_sectors(a),
        "",
        section_dragons(d, a),
        "",
        section_global(g, n),
        "",
    ]

    if llm:
        vp = llm.get("viewpoint") or []
        body = ["## 五、复盘观点（AI 生成，仅供参考，不构成投资建议）", ""]
        for item in vp:
            heading = str(item.get("heading", "")).strip()
            text = str(item.get("body", "")).strip()
            if not heading and not text:
                continue
            body.append(f"**{heading}**" if heading else "")
            if text:
                body.append(text)
            body.append("")
        if len(body) <= 2:
            body.append("（本轮大模型未返回有效观点条目，观点章节留空。）")
            body.append("")

        plan = llm.get("plan") or []
        body += ["## 六、明日预案（情景推演，非操作建议）", "", "| 情景 | 触发条件 | 应对思路 |", "| --- | --- | --- |"]
        for row in plan:
            body.append(
                f"| {yaml_str(row.get('scenario', ''))} | {yaml_str(row.get('trigger', ''))} | {yaml_str(row.get('action', ''))} |"
            )
        if not plan:
            body.append("| — | — | （本轮大模型未返回预案，留空） |")
        watch = str(llm.get("watchpoints") or "").strip()
        if watch:
            body += ["", f"**三个关键观察点**：{watch}"]
        body += ["", "> 以上情景为基于当日公开数据的推演与风格化解读，不构成任何投资建议；据此操作，风险自负。"]
    else:
        body = [
            "## 五、复盘观点",
            "",
            "> 观点待生成：本轮大模型接口不可用（`LLM_API_KEY` / `LLM_API_BASE` 未配置或调用失败）。",
            "> 为避免编造观点，此处留空；数据章节不受影响，仍为当日真实数据。",
            "",
            "## 六、明日预案",
            "",
            "> 同上，待大模型可用后自动生成。",
        ]

    body += ["", "## 七、待补充", "", "- [ ] 标的观察与历史战绩结算（需结合实盘人工记录）", ""]
    return "\n".join(fm + body), engine


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="生成当日复盘 MDX")
    ap.add_argument("--date", help="指定交易日 YYYY-MM-DD（默认按数据自动推断）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的当日复盘")
    ap.add_argument("--allow-stale", action="store_true", help="数据是旧的也照样生成（调试用）")
    args = ap.parse_args()

    a = load("ashare.json")
    d = load("dragons.json")
    g = load("global_snapshot.json")
    n = load("news.json")

    if not a.get("indices") and not a.get("breadth"):
        log.warning("ashare.json 没有指数/涨跌家数数据，跳过生成")
        return 1

    day = parse_day(args.date) or parse_day(a.get("tradeDay")) or expected_trade_day()
    expect = expected_trade_day()
    age = hours_since(a.get("updatedAt") or "")

    if not args.allow_stale:
        if a.get("stale"):
            log.warning("ashare.json 标记为 stale，跳过生成")
            return 1
        if day != expect:
            log.warning("数据交易日 %s 与应复盘交易日 %s 不一致，跳过生成（数据未更新）", day, expect)
            return 1
        if age is not None and age > MAX_DATA_AGE_HOURS:
            log.warning("ashare.json 已 %.1f 小时未更新（阈值 %s 小时），跳过生成", age, MAX_DATA_AGE_HOURS)
            return 1

    REVIEWS.mkdir(parents=True, exist_ok=True)
    target = REVIEWS / f"{day.isoformat()}.mdx"
    if target.exists() and not args.force:
        log.info("%s 已存在，跳过（要重刷请加 --force）", target.name)
        return 0

    digest = build_digest(a, d, g, n, day)
    llm = None
    if os.getenv("DISABLE_LLM", "").lower() not in ("1", "true", "yes"):
        raw = llm_chat(SYSTEM_PROMPT, f"{digest}\n\n{OUTPUT_SCHEMA}")
        llm = parse_llm_json(raw or "")
        if raw and not llm:
            log.warning("大模型输出无法解析为 JSON，观点章节降级留空")
    else:
        log.info("已通过 DISABLE_LLM 关闭观点生成")

    mdx, engine = build_mdx(day, a, d, g, n, llm)
    target.write_text(mdx, encoding="utf-8")
    log.info("已生成 %s（engine=%s，%d 字）", target.name, engine, len(mdx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
