"""
A股盘面 / 主线板块 / 龙虎榜 / 资金流 抓取

数据源（按可用性自动切换，任一失败则保留上一版 JSON）：
  指数快照：新浪财经（国内可用）→ 东方财富 → 新浪日K
  市场宽度：乐咕乐股 → 全 A 快照统计
  行业板块：新浪行业板块 → 东方财富行业/概念板块
  龙虎榜：东方财富 LHB
  融资余额：沪深交易所（sse）

akshare 的函数名与域名偶有调整，这里对每个数据需求列出多个候选，
并通过 retry_call 应对代理/数据源的偶发抖动。

输出：src/data/ashare.json、src/data/dragons.json
"""

from __future__ import annotations

import sys
from datetime import timedelta

from common import (
    batch_from_args,
    clip,
    merge_with_previous,
    now_cn,
    now_iso,
    optional_import,
    retry_call,
    setup_logging,
    to_float,
    write_json,
)

log = setup_logging("fetch_ashare")

OUT_ASHARE = "ashare.json"
OUT_DRAGONS = "dragons.json"

# (新浪代码, 东财代码, 显示名)
TARGET_INDICES = [
    ("sh000001", "000001", "上证指数"),
    ("sz399001", "399001", "深证成指"),
    ("sz399006", "399006", "创业板指"),
    ("sh000300", "000300", "沪深300"),
    ("sh000688", "000688", "科创50"),
    ("sz899050", "899050", "北证50"),
]


# --------------------------------------------------------------------------
# 通用
# --------------------------------------------------------------------------
def call_first(ak, names, **kwargs):
    for name in names:
        fn = getattr(ak, name, None)
        if fn is None:
            continue
        df = retry_call(fn, times=3, delay=1.0, label=name, **kwargs)
        if df is not None and len(df) > 0:
            log.info("命中接口 %s", name)
            return df, name
        log.warning("接口 %s 无数据", name)
    return None, None


def find_col(df, keywords):
    """在 DataFrame 列里模糊查找包含任一关键词的第一列。"""
    for kw in keywords:
        for c in df.columns:
            if kw in str(c):
                return c
    return None


def to_yi(v):
    """把元换算成亿元；已是亿量级（<1e5）时原样返回。"""
    v = to_float(v)
    if v is None:
        return None
    return round(v / 1e8, 2) if abs(v) > 1e6 else round(v, 2)


def to_wan(v):
    """把元换算成万元。"""
    v = to_float(v)
    if v is None:
        return None
    return round(v / 1e4, 2) if abs(v) > 1e6 else round(v, 2)


# --------------------------------------------------------------------------
# 指数快照
# --------------------------------------------------------------------------
def _indices_from_sina(df):
    code_col = find_col(df, ["代码"])
    name_col = find_col(df, ["名称"])
    price_col = find_col(df, ["最新价"])
    chg_col = find_col(df, ["涨跌额"])
    pct_col = find_col(df, ["涨跌幅"])
    prev_col = find_col(df, ["昨收"])
    high_col = find_col(df, ["最高"])
    low_col = find_col(df, ["最低"])
    amt_col = find_col(df, ["成交额"])
    by_code = {str(r.get(code_col, "")).strip(): r for _, r in df.iterrows()}

    out = []
    for sina_code, em_code, name in TARGET_INDICES:
        row = by_code.get(sina_code)
        if row is None:
            continue
        close = to_float(row.get(price_col))
        if close is None:
            continue
        prev = to_float(row.get(prev_col)) or 0
        high = to_float(row.get(high_col))
        low = to_float(row.get(low_col))
        amp = round((high - low) / prev * 100, 2) if (high and low and prev) else None
        out.append(
            {
                "code": em_code,
                "name": name,
                "close": round(close, 2),
                "change": round(to_float(row.get(chg_col)) or 0, 2),
                "changePct": round(to_float(row.get(pct_col)) or 0, 2),
                "amount": to_yi(row.get(amt_col)) if amt_col else None,
                "amplitude": amp,
            }
        )
    return out


def _indices_from_em(df):
    name_col = find_col(df, ["名称", "指数名称"]) or "名称"
    code_col = find_col(df, ["代码"]) or "代码"
    price_col = find_col(df, ["最新价", "收盘价"]) or "最新价"
    chg_col = find_col(df, ["涨跌额"])
    pct_col = find_col(df, ["涨跌幅"])
    amt_col = find_col(df, ["成交额"])
    amp_col = find_col(df, ["振幅"])
    want = {name: (em_code, name) for _, em_code, name in TARGET_INDICES}

    out = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        if name not in want:
            continue
        em_code, disp = want[name]
        out.append(
            {
                "code": em_code,
                "name": disp,
                "close": to_float(row.get(price_col)) or 0,
                "change": round(to_float(row.get(chg_col)) or 0, 2),
                "changePct": round(to_float(row.get(pct_col)) or 0, 2),
                "amount": to_yi(row.get(amt_col)) if amt_col else None,
                "amplitude": round(to_float(row.get(amp_col)), 2) if amp_col else None,
            }
        )
    return out


def fetch_indices(ak):
    # 1) 新浪（国内网络可用）
    df, _ = call_first(ak, ["stock_zh_index_spot_sina"])
    if df is not None:
        out = _indices_from_sina(df)
        if out:
            return out
    # 2) 东方财富
    df, _ = call_first(ak, ["stock_zh_index_spot_em"], symbol="沪深重要指数")
    if df is not None:
        out = _indices_from_em(df)
        if out:
            return out
    return []


def fetch_market_amount(ak):
    """两市成交额（亿元）= 上证 + 深证成指。"""
    df, _ = call_first(ak, ["stock_zh_index_spot_sina"])
    if df is None:
        return None
    code_col = find_col(df, ["代码"])
    amt_col = find_col(df, ["成交额"])
    if not code_col or not amt_col:
        return None
    total = 0.0
    hit = False
    for _, row in df.iterrows():
        if str(row.get(code_col, "")).strip() in ("sh000001", "sz399001"):
            v = to_yi(row.get(amt_col))
            if v:
                total += v
                hit = True
    return round(total, 2) if hit else None


# --------------------------------------------------------------------------
# 市场宽度（涨跌家数 / 涨停跌停）
# --------------------------------------------------------------------------
def fetch_breadth(ak):
    df, _ = call_first(ak, ["stock_market_activity_legu"])
    if df is not None:
        cols = list(df.columns)
        data = {}
        if len(cols) >= 2:
            for _, row in df.iterrows():
                k = str(row[cols[0]])
                v = to_float(row[cols[1]])
                if v is not None:
                    data[k] = v
        mapping = {
            "up": ["上涨"],
            "down": ["下跌"],
            "flat": ["平盘"],
            "limitUp": ["涨停"],
            "limitDown": ["跌停"],
        }
        out = {}
        for key, kws in mapping.items():
            for k, v in data.items():
                if any(kw in k for kw in kws):
                    out[key] = int(v)
                    break
        if {"up", "down"} <= out.keys():
            return {
                "up": out.get("up", 0),
                "down": out.get("down", 0),
                "flat": out.get("flat", 0),
                "limitUp": out.get("limitUp", 0),
                "limitDown": out.get("limitDown", 0),
                "total": out.get("up", 0) + out.get("down", 0) + out.get("flat", 0),
            }

    df, _ = call_first(ak, ["stock_zh_a_spot_em"])
    if df is None:
        return None
    pct_col = find_col(df, ["涨跌幅"])
    code_col = find_col(df, ["代码"])
    if not pct_col:
        return None

    up = down = flat = lu = ld = 0
    for _, row in df.iterrows():
        p = to_float(row.get(pct_col))
        if p is None:
            continue
        code = str(row.get(code_col, "")) if code_col else ""
        limit = 19.5 if code.startswith(("300", "688", "8")) else 9.8
        if p > 0:
            up += 1
        elif p < 0:
            down += 1
        else:
            flat += 1
        if p >= limit:
            lu += 1
        elif p <= -limit:
            ld += 1
    return {"up": up, "down": down, "flat": flat, "limitUp": lu, "limitDown": ld, "total": up + down + flat}


# --------------------------------------------------------------------------
# 主线板块
# --------------------------------------------------------------------------
def fetch_sectors(ak, limit=6):
    # 1) 新浪行业板块（国内可用）
    df, _ = call_first(ak, ["stock_sector_spot"], indicator="新浪行业")
    if df is not None:
        out = _sectors_from_sina(df, limit)
        if out:
            return out
    # 2) 东财行业/概念板块
    df, _ = call_first(ak, ["stock_board_industry_name_em", "stock_board_concept_name_em"])
    if df is not None:
        out = _sectors_from_em(df, limit)
        if out:
            return out
    return []


def _split_up_down(rows, limit):
    """按真实涨跌幅拆分领涨/领跌：只保留真正上涨 / 真正下跌的板块。"""
    ups = [r for r in rows if r["changePct"] > 0][:limit]
    downs = [r for r in rows if r["changePct"] < 0][:limit]
    downs.sort(key=lambda r: r["changePct"])
    return ups + downs


def _sectors_from_sina(df, limit):
    name_col = find_col(df, ["板块"])
    pct_col = find_col(df, ["涨跌幅"])
    lead_name_col = find_col(df, ["股票名称"])
    lead_code_col = find_col(df, ["股票代码"])
    if not name_col or not pct_col:
        return []
    df = df.copy()
    df["_pct"] = df[pct_col].map(to_float)
    df = df.dropna(subset=["_pct"]).sort_values(by="_pct", ascending=False)

    rows = [_sina_sector_row(r, name_col, "_pct", lead_name_col, lead_code_col) for _, r in df.iterrows()]
    return _split_up_down(rows, limit)


def _sina_sector_row(row, name_col, pct_col, lead_name_col, lead_code_col):
    pct = round(to_float(row.get(pct_col)) or 0, 2)
    leading = []
    nm = row.get(lead_name_col)
    cd = row.get(lead_code_col)
    if isinstance(nm, str) and nm.strip():
        leading.append(str(cd or "").strip() or nm.strip())
    return {
        "name": str(row.get(name_col, "")).strip(),
        "changePct": pct,
        "direction": "flat" if pct == 0 else ("up" if pct > 0 else "down"),
        "driver": "",
        "leading": leading,
    }


def _sectors_from_em(df, limit):
    name_col = find_col(df, ["板块名称", "名称"])
    pct_col = find_col(df, ["涨跌幅"])
    lead_col = find_col(df, ["领涨股票"])
    if not name_col or not pct_col:
        return []
    df = df.dropna(subset=[pct_col])
    try:
        df = df.sort_values(by=pct_col, ascending=False)
    except Exception:  # noqa: BLE001
        pass

    def pack(row):
        pct = round(to_float(row.get(pct_col)) or 0, 2)
        leading = []
        if lead_col:
            v = row.get(lead_col)
            if isinstance(v, str) and v.strip():
                leading = [x.strip() for x in v.split(",")][:2]
        return {
            "name": str(row.get(name_col, "")).strip(),
            "changePct": pct,
            "direction": "flat" if pct == 0 else ("up" if pct > 0 else "down"),
            "driver": "",
            "leading": leading,
        }

    rows = [pack(r) for _, r in df.iterrows()]
    return _split_up_down(rows, limit)


# --------------------------------------------------------------------------
# 龙虎榜 / 资金流 / 融资余额
# --------------------------------------------------------------------------
def fetch_dragons(ak, day: str, stat_df=None):
    # 1) 东财当日龙虎榜明细（字段最全）
    df, _ = call_first(ak, ["stock_lhb_detail_em"], start_date=day, end_date=day)
    if df is not None:
        items = _dragons_from_em_detail(df)
        if items:
            return items

    # 2) 东财近一月统计，过滤最近上榜日 == 最近交易日
    if stat_df is not None:
        items = _dragons_from_em_statistic(stat_df, day)
        if items:
            # 用新浪当日明细补上榜原因
            reason_map = _sina_lhb_reasons(ak, day)
            for it in items:
                it["reason"] = reason_map.get(it["code"], it["reason"])
            return items

    # 3) 新浪当日龙虎榜（无净买额，仅上榜名单与原因）
    return [
        {"code": c, "name": n, "close": p, "changePct": 0, "netBuy": 0, "reason": r}
        for c, n, p, r in _sina_lhb_reasons(ak, day, full=True)
    ]


def _dragons_from_em_detail(df):
    code_col = find_col(df, ["代码"])
    name_col = find_col(df, ["名称"])
    price_col = find_col(df, ["收盘价"])
    pct_col = find_col(df, ["涨跌幅"])
    net_col = find_col(df, ["机构买入净额", "龙虎榜净买额", "净买额"])
    buy_col = find_col(df, ["机构买入总额", "买入额"])
    sell_col = find_col(df, ["机构卖出总额", "卖出额"])
    reason_col = find_col(df, ["上榜原因", "解读"])

    if net_col:
        df = df.dropna(subset=[net_col])
        try:
            df = df.sort_values(by=net_col, ascending=False)
        except Exception:  # noqa: BLE001
            pass

    items = []
    for _, row in df.head(12).iterrows():
        items.append(
            {
                "code": str(row.get(code_col, "")).strip(),
                "name": str(row.get(name_col, "")).strip(),
                "close": to_float(row.get(price_col)) or 0,
                "changePct": round(to_float(row.get(pct_col)) or 0, 2),
                "netBuy": to_wan(row.get(net_col)) or 0,
                "buyAmount": to_wan(row.get(buy_col)) if buy_col else None,
                "sellAmount": to_wan(row.get(sell_col)) if sell_col else None,
                "reason": str(row.get(reason_col, "")).strip()[:60] if reason_col else "",
            }
        )
    return items


def _dragons_from_em_statistic(df, day: str):
    iso_day = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    date_col = find_col(df, ["最近上榜日"])
    if not date_col:
        return []
    df = df[df[date_col].astype(str).str.startswith(iso_day)]
    if len(df) == 0:
        return []
    net_col = find_col(df, ["龙虎榜净买额"])
    if net_col:
        try:
            df = df.sort_values(by=net_col, ascending=False)
        except Exception:  # noqa: BLE001
            pass
    code_col = find_col(df, ["代码"])
    name_col = find_col(df, ["名称"])
    price_col = find_col(df, ["收盘价"])
    pct_col = find_col(df, ["涨跌幅"])
    buy_col = find_col(df, ["龙虎榜买入额"])
    sell_col = find_col(df, ["龙虎榜卖出额"])

    items = []
    for _, row in df.head(12).iterrows():
        items.append(
            {
                "code": str(row.get(code_col, "")).strip(),
                "name": str(row.get(name_col, "")).strip(),
                "close": to_float(row.get(price_col)) or 0,
                "changePct": round(to_float(row.get(pct_col)) or 0, 2),
                "netBuy": to_wan(row.get(net_col)) or 0,
                "buyAmount": to_wan(row.get(buy_col)) if buy_col else None,
                "sellAmount": to_wan(row.get(sell_col)) if sell_col else None,
                "reason": "近一月上榜统计口径",
            }
        )
    return items


def _sina_lhb_reasons(ak, day: str, full: bool = False):
    """新浪当日龙虎榜：默认返回 {代码: 原因}；full=True 返回完整记录。"""

    def _call():
        return ak.stock_lhb_detail_daily_sina(date=day)

    df = retry_call(_call, times=2, delay=1.0, label="sina_lhb")
    if df is None or len(df) == 0:
        return {}
    code_col = find_col(df, ["股票代码"])
    name_col = find_col(df, ["股票名称"])
    price_col = find_col(df, ["收盘价"])
    reason_col = find_col(df, ["指标"])
    if not code_col:
        return {}
    out = {}
    for _, row in df.iterrows():
        code = str(row.get(code_col, "")).strip()
        if not code:
            continue
        entry = (
            code,
            str(row.get(name_col, "")).strip() if name_col else "",
            to_float(row.get(price_col)) or 0,
            str(row.get(reason_col, "")).strip()[:60] if reason_col else "",
        )
        out[code] = entry[3]
        if full:
            out.setdefault("_rows", []).append(entry)
    if full:
        rows = out.pop("_rows", [])
        return rows
    return out


def fetch_fund_rank(ak):
    df, _ = call_first(ak, ["stock_individual_fund_flow_rank"], indicator="今日")
    if df is None:
        return [], []
    code_col = find_col(df, ["代码"])
    name_col = find_col(df, ["名称"])
    pct_col = find_col(df, ["涨跌幅"])
    net_col = find_col(df, ["主力净流入-净额", "主力净流入"])
    if not net_col:
        return [], []

    def pack(row):
        return {
            "code": str(row.get(code_col, "")).strip(),
            "name": str(row.get(name_col, "")).strip(),
            "changePct": round(to_float(row.get(pct_col)) or 0, 2),
            "mainNet": to_yi(row.get(net_col)) or 0,
        }

    try:
        df = df.sort_values(by=net_col, ascending=False)
    except Exception:  # noqa: BLE001
        return [], []
    top_in = [pack(r) for _, r in df.head(5).iterrows()]
    top_out = [pack(r) for _, r in df.tail(5).iloc[::-1].iterrows()]
    return top_in, top_out


def fetch_margin(ak):
    today = now_cn().date()
    start = (today - timedelta(days=20)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    df, _ = call_first(ak, ["stock_margin_sse"], start_date=start, end_date=end)
    if df is None:
        return None
    col = find_col(df, ["融资余额"])
    if not col or len(df) < 2:
        return None
    last = to_float(df[col].iloc[-1])
    prev = to_float(df[col].iloc[-2])
    if last is None or prev is None:
        return None
    return {"balance": to_yi(last) or 0, "change": round((to_yi(last) or 0) - (to_yi(prev) or 0), 2)}


# --------------------------------------------------------------------------
# 情绪分（由真实盘面数据按可解释规则计算，不做预测）
# --------------------------------------------------------------------------
def build_sentiment(breadth, volume, prev_volume):
    drivers = []
    score = 50.0

    if breadth and (breadth.get("up") or breadth.get("down")):
        up, down = breadth.get("up", 0), breadth.get("down", 0)
        if up + down > 0:
            ratio = up / (up + down)
            score += clip((ratio - 0.5) * 80, -25, 25)
            drivers.append(f"涨跌家数 {up} / {down}，上涨占比 {ratio * 100:.1f}%")
        lu = breadth.get("limitUp", 0)
        score += clip((lu - 50) * 0.2, -8, 8)
        drivers.append(f"涨停 {lu} 家、跌停 {breadth.get('limitDown', 0)} 家")

    if volume and prev_volume:
        chg = (volume - prev_volume) / prev_volume * 100 if prev_volume else 0
        score += clip(chg * 0.6, -12, 12)
        drivers.append(f"两市成交 {volume:.0f} 亿，环比 {chg:+.2f}%")

    score = int(clip(round(score), 0, 100))
    if score >= 70:
        label = "过热"
    elif score >= 55:
        label = "偏强"
    elif score >= 45:
        label = "中性"
    elif score >= 30:
        label = "中性偏弱"
    else:
        label = "冰点"
    return {"score": score, "label": label, "desc": "", "drivers": drivers}


def resolve_trade_day(ak):
    """
    解析「最近交易日」：凌晨 / 节假日后当天还没有龙虎榜与收盘数据，
    不能直接用日历日。优先取东财近一月统计里最大的最近上榜日。
    返回 (dayYYYYMMDD, stat_df)。
    """
    stat_df, _ = call_first(ak, ["stock_lhb_stock_statistic_em"], symbol="近一月")
    if stat_df is not None:
        col = find_col(stat_df, ["最近上榜日"])
        if col:
            try:
                dmax = str(stat_df[col].astype(str).max())[:10].replace("-", "")
                if len(dmax) == 8 and dmax.isdigit():
                    return dmax, stat_df
            except Exception:  # noqa: BLE001
                pass
    # 回退：按工作日往前找
    d = now_cn().date()
    for _ in range(7):
        if d.weekday() < 5:
            return d.strftime("%Y%m%d"), None
        d -= timedelta(days=1)
    return now_cn().strftime("%Y%m%d"), None


# --------------------------------------------------------------------------
def main() -> int:
    ak = optional_import("akshare")
    if ak is None:
        log.warning("未安装 akshare，跳过 A股抓取（保留上一版 JSON）")
        log.warning("安装：pip install akshare 或 npm run bootstrap:py")
        return 1

    batch = batch_from_args("A股收盘")
    day, stat_df = resolve_trade_day(ak)
    log.info("最近交易日：%s", day)

    # ---------- ashare.json ----------
    indices = fetch_indices(ak)
    breadth = fetch_breadth(ak)
    sectors = fetch_sectors(ak)
    amount = fetch_market_amount(ak)
    margin = fetch_margin(ak)

    if not indices and not breadth:
        log.warning("A股核心数据全部抓取失败，保留上一版 JSON")
        return 1

    prev = merge_with_previous({}, OUT_ASHARE, ["volume", "margin", "breadth", "indices", "sectors"])
    prev_volume = (prev.get("volume") or {}).get("amount")
    volume = amount if amount else prev_volume
    change_pct = 0.0
    if volume and prev_volume:
        change_pct = round((volume - prev_volume) / prev_volume * 100, 2)

    ashare_payload = {
        "updatedAt": now_iso(),
        "batch": batch,
        "source": "akshare",
        "stale": False,
        "note": "延迟数据，来自公开免费数据源（新浪财经 / 东方财富），仅用于复盘记录。",
        "indices": indices or prev.get("indices", []),
        "breadth": breadth or prev.get("breadth", {"up": 0, "down": 0, "flat": 0, "limitUp": 0, "limitDown": 0}),
        "volume": {"amount": volume or 0, "prevAmount": prev_volume or 0, "changePct": change_pct},
        "margin": margin or prev.get("margin"),
        "sentiment": build_sentiment(breadth, volume, prev_volume),
        "sectors": sectors or prev.get("sectors", []),
        "fundFlow": {
            "main": {"net": 0, "unit": "亿元", "note": "东财主力资金接口当日不可用时留空"},
            "north": {"net": 0, "unit": "亿元", "note": "北向实时数据已停止披露，改由沪深港通月度/季度口径替代"},
        },
    }
    write_json(OUT_ASHARE, ashare_payload)
    log.info("已写入 %s（指数 %d / 板块 %d）", OUT_ASHARE, len(ashare_payload["indices"]), len(ashare_payload["sectors"]))

    # ---------- dragons.json ----------
    items = fetch_dragons(ak, day, stat_df)
    top_in, top_out = fetch_fund_rank(ak)
    dragons_payload = {
        "updatedAt": now_iso(),
        "batch": batch,
        "source": "akshare",
        "stale": False,
        "note": "延迟数据，龙虎榜为收盘后披露口径。",
        "items": items,
        "topInflow": top_in,
        "topOutflow": top_out,
    }
    dragons_payload = merge_with_previous(dragons_payload, OUT_DRAGONS, ["items", "topInflow", "topOutflow"])
    write_json(OUT_DRAGONS, dragons_payload)
    log.info("已写入 %s（龙虎榜 %d 条）", OUT_DRAGONS, len(dragons_payload["items"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
