"""
全球市场快照抓取：美股 / 中概 / 港股·亚太 / 商品·汇率

数据源（按可用性自动切换，任一失败不影响其它）：
  1) 新浪财经（国内网络可用）：美股指数日K、港股指数快照
  2) akshare 东方财富：全球指数、美股现货
  3) yfinance —— 海外 CI（GitHub Actions）环境可用
  4) Stooq CSV —— 兜底，免费无 key

自适应保护：同一数据源连续失败 2 次后，本轮不再尝试，避免网络不通时逐个超时。

输出：src/data/global_snapshot.json
"""

from __future__ import annotations

import os
import sys

from common import (
    batch_from_args,
    call_with_deadline,
    merge_with_previous,
    now_iso,
    optional_import,
    retry_call,
    setup_logging,
    to_float,
    write_json,
)

log = setup_logging("fetch_global")
OUT = "global_snapshot.json"

GROUPS = [
    {
        "market": "美股",
        "currency": "USD",
        "items": [
            ("^GSPC", "标普500"),
            ("^IXIC", "纳斯达克"),
            ("^DJI", "道琼斯"),
            ("^VIX", "恐慌指数"),
        ],
    },
    {
        "market": "中概",
        "currency": "USD",
        "items": [
            ("KWEB", "中概互联网ETF"),
            ("BABA", "阿里巴巴"),
            ("PDD", "拼多多"),
            ("JD", "京东"),
        ],
    },
    {
        "market": "港股 / 亚太",
        "currency": "HKD / 本币",
        "items": [
            ("^HSI", "恒生指数"),
            ("^N225", "日经225"),
            ("^KS11", "韩国综合"),
            ("^TWII", "台湾加权"),
        ],
    },
    {
        "market": "商品 / 汇率",
        "currency": "USD",
        "items": [
            ("GC=F", "COMEX黄金"),
            ("CL=F", "WTI原油"),
            ("USDCNY=X", "美元/人民币"),
            ("BTC-USD", "比特币"),
        ],
    },
]

# 新浪美股指数代码
SINA_US = {"^GSPC": ".INX", "^IXIC": ".IXIC", "^DJI": ".DJI"}
# 新浪港股指数名称（子串匹配）
SINA_HK = {"^HSI": "恒生指数"}

# 东财全球指数名称 / 美股名称映射
EM_GLOBAL_NAME_MAP = {
    "标普500": "标普500",
    "纳斯达克": "纳斯达克",
    "道琼斯": "道琼斯",
    "恒生指数": "恒生指数",
    "日经225": "日经225",
    "韩国综合": "韩国综合",
    "台湾加权": "台湾加权",
}
EM_US_NAME_MAP = {
    "阿里巴巴": "阿里巴巴",
    "拼多多": "拼多多",
    "京东": "京东",
}

STOOQ_MAP = {
    "^GSPC": "^spx",
    "^IXIC": "^ndq",
    "^DJI": "^dji",
    "^VIX": "^vix",
    "KWEB": "kweb.us",
    "BABA": "baba.us",
    "PDD": "pdd.us",
    "JD": "jd.us",
    "^HSI": "^hsi",
    "^N225": "^nkx",
    "^KS11": "^kospi",
    "^TWII": "^twse",
    "GC=F": "gc.f",
    "CL=F": "cl.f",
    "USDCNY=X": "usdcny",
    "BTC-USD": "btcusd",
}


class SourceGuard:
    """连续失败达到阈值后，本轮不再尝试该数据源。"""

    def __init__(self, threshold: int = 2):
        self.threshold = threshold
        self._fails: dict[str, int] = {}

    def disabled(self, name: str) -> bool:
        return self._fails.get(name, 0) >= self.threshold

    def fail(self, name: str, reason: str = "") -> None:
        n = self._fails.get(name, 0) + 1
        self._fails[name] = n
        if n >= self.threshold:
            log.warning("数据源 %s 连续失败 %d 次，本轮停用（%s）", name, n, reason)

    def ok(self, name: str) -> None:
        self._fails[name] = 0


GUARD = SourceGuard()


# --------------------------------------------------------------------------
# 数据源 1：新浪财经
# --------------------------------------------------------------------------
def _sina_quote(price, prev, chg, as_of=""):
    if price is None or prev in (None, 0):
        return None
    change = (price - prev) if chg is None else chg
    return {
        "price": round(price, 4),
        "change": round(change, 4),
        "changePct": round((change / prev * 100) if prev else 0, 2),
        "asOf": as_of or "",
    }


def fetch_sina_us(ak, symbol: str):
    if GUARD.disabled("sina_us"):
        return None
    code = SINA_US.get(symbol)
    if not code:
        return None

    def _call():
        return ak.index_us_stock_sina(symbol=code)

    df = retry_call(_call, times=2, delay=1.0, label=f"sina_us {code}")
    if df is None or len(df) < 2:
        GUARD.fail("sina_us", f"{code} 无数据")
        return None
    closes = [to_float(c) for c in df["close"].tolist() if to_float(c) is not None]
    if len(closes) < 2:
        GUARD.fail("sina_us", f"{code} 收盘价不足")
        return None
    GUARD.ok("sina_us")
    as_of = str(df["date"].iloc[-1]) if "date" in df.columns else ""
    return _sina_quote(closes[-1], closes[-2], None, as_of)


def fetch_sina_hk(ak, symbol: str, name: str):
    if GUARD.disabled("sina_hk"):
        return None
    target = SINA_HK.get(symbol)
    if not target:
        return None

    def _call():
        return ak.stock_hk_index_spot_sina()

    df = retry_call(_call, times=2, delay=1.0, label="sina_hk")
    if df is None or len(df) == 0:
        GUARD.fail("sina_hk", "无数据")
        return None
    name_col = next((c for c in df.columns if "名称" in str(c)), None)
    if not name_col:
        GUARD.fail("sina_hk", "无名称列")
        return None
    for _, row in df.iterrows():
        if target in str(row.get(name_col, "")):
            q = _sina_quote(
                to_float(row.get("最新价")),
                to_float(row.get("昨收")),
                to_float(row.get("涨跌额")),
            )
            if q:
                GUARD.ok("sina_hk")
                return q
    GUARD.fail("sina_hk", f"未匹配 {target}")
    return None


# --------------------------------------------------------------------------
# 数据源 2：东方财富
# --------------------------------------------------------------------------
class EastmoneyCache:
    def __init__(self):
        self.ak = optional_import("akshare")
        self._caches: dict[str, object | None] = {}
        self._loaded: set[str] = set()

    def _load(self, key: str, fn):
        if key in self._loaded or self.ak is None:
            return self._caches.get(key)
        # index_global_spot_em 这类接口内部会串行请求上百个标的，单次调用可能要七八分钟，
        # socket 超时拦不住（每一小步都在超时内，只是步数多）。这里给它一个总预算，
        # 超了就放弃、换下一条数据源。
        deadline = float(os.getenv("EM_DEADLINE", "45"))
        self._caches[key] = call_with_deadline(
            lambda: retry_call(fn, times=2, delay=1.0, label=f"em {key}"),
            deadline,
            label=f"em {key}",
        )
        self._loaded.add(key)
        return self._caches.get(key)

    @staticmethod
    def _col(df, keywords):
        for kw in keywords:
            for c in df.columns:
                if kw in str(c):
                    return c
        return None

    def _from_df(self, df, target, name_col, price_col, chg_col, pct_col):
        if df is None or not len(df) or not name_col or not price_col:
            return None
        for _, row in df.iterrows():
            if target not in str(row.get(name_col, "")).strip():
                continue
            price = to_float(row.get(price_col))
            if price is None:
                continue
            prev_col = next((c for c in df.columns if "昨收" in str(c)), None)
            prev = to_float(row.get(prev_col)) if prev_col else None
            chg = to_float(row.get(chg_col)) if chg_col else None
            q = _sina_quote(price, prev, chg)
            if q is None:
                pct = to_float(row.get(pct_col)) if pct_col else None
                base = price - (chg or 0) if chg is not None else None
                q = {
                    "price": round(price, 4),
                    "change": round(chg or 0, 4),
                    "changePct": round(pct or 0, 2),
                    "asOf": "",
                }
                if base:
                    q["changePct"] = round(chg / base * 100, 2)
            return q
        return None

    def lookup(self, symbol: str, name: str):
        # 全球指数
        df = self._load("global", lambda: self.ak.index_global_spot_em())
        if df is not None and len(df):
            target = EM_GLOBAL_NAME_MAP.get(name)
            if target:
                q = self._from_df(
                    df,
                    target,
                    self._col(df, ["名称"]),
                    self._col(df, ["最新价"]),
                    self._col(df, ["涨跌额"]),
                    self._col(df, ["涨跌幅"]),
                )
                if q:
                    return q
        # 美股现货（中概）
        df = self._load("us", lambda: self.ak.stock_us_spot_em())
        if df is not None and len(df):
            target = EM_US_NAME_MAP.get(name)
            if target:
                q = self._from_df(
                    df,
                    target,
                    self._col(df, ["名称"]),
                    self._col(df, ["最新价"]),
                    self._col(df, ["涨跌额"]),
                    self._col(df, ["涨跌幅"]),
                )
                if q:
                    return q
        return None


# --------------------------------------------------------------------------
# 数据源 3 / 4：yfinance 与 Stooq
# --------------------------------------------------------------------------
def fetch_yfinance(symbol: str):
    if GUARD.disabled("yfinance"):
        return None
    yf = optional_import("yfinance")
    if yf is None:
        GUARD.fail("yfinance", "未安装")
        return None

    def _call():
        return yf.Ticker(symbol).history(period="7d", auto_adjust=False)

    hist = retry_call(_call, times=2, delay=1.0, label=f"yf {symbol}")
    if hist is None or len(hist) < 2:
        GUARD.fail("yfinance", f"{symbol} 数据不足")
        return None
    closes = [to_float(c) for c in hist["Close"].tolist() if to_float(c) is not None]
    if len(closes) < 2 or not closes[-2]:
        GUARD.fail("yfinance", f"{symbol} 收盘价不足")
        return None
    GUARD.ok("yfinance")
    last, prev = closes[-1], closes[-2]
    return {
        "price": round(last, 4),
        "change": round(last - prev, 4),
        "changePct": round((last - prev) / prev * 100, 2),
        "asOf": str(hist.index[-1].date()) if hasattr(hist.index[-1], "date") else "",
    }


def fetch_stooq(symbol: str):
    if GUARD.disabled("stooq"):
        return None
    pd = optional_import("pandas")
    code = STOOQ_MAP.get(symbol)
    if pd is None or not code:
        GUARD.fail("stooq", "不可用")
        return None

    def _call():
        return pd.read_csv(f"https://stooq.com/q/d/l/?s={code}&i=d")

    df = retry_call(_call, times=2, delay=1.0, label=f"stooq {symbol}")
    if df is None or len(df) < 2:
        GUARD.fail("stooq", f"{symbol} 数据不足")
        return None
    try:
        last = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2])
    except Exception as exc:  # noqa: BLE001
        GUARD.fail("stooq", str(exc)[:80])
        return None
    if not prev:
        return None
    GUARD.ok("stooq")
    return {
        "price": round(last, 4),
        "change": round(last - prev, 4),
        "changePct": round((last - prev) / prev * 100, 2),
        "asOf": str(df["Date"].iloc[-1]),
    }


# --------------------------------------------------------------------------
def build() -> dict:
    ak = optional_import("akshare")
    em = EastmoneyCache()
    if ak is None:
        log.info("未安装 akshare，跳过新浪 / 东财数据源")

    def resolve(symbol: str, name: str):
        if ak is not None:
            q = fetch_sina_us(ak, symbol)
            if q:
                return q, "sina"
            q = fetch_sina_hk(ak, symbol, name)
            if q:
                return q, "sina"
            q = em.lookup(symbol, name)
            if q:
                return q, "eastmoney"
        q = fetch_yfinance(symbol)
        if q:
            return q, "yfinance"
        q = fetch_stooq(symbol)
        if q:
            return q, "stooq"
        return None, ""

    groups = []
    ok_total = 0
    sources: set[str] = set()
    for g in GROUPS:
        items = []
        for symbol, name in g["items"]:
            q, src = resolve(symbol, name)
            if q:
                ok_total += 1
                sources.add(src)
                items.append({"symbol": symbol, "name": name, **q})
            else:
                log.warning("全球标的抓取失败：%s %s", symbol, name)
        groups.append({"market": g["market"], "currency": g["currency"], "items": items})

    if ok_total == 0:
        log.warning("全球快照无任何有效数据，保留上一版")
        return {}

    return {
        "updatedAt": now_iso(),
        "batch": batch_from_args("美股收盘"),
        "source": "+".join(sorted(sources)),
        "stale": False,
        "note": "上一交易日收盘的延迟数据，用于复盘记录。",
        "groups": groups,
    }


def merge_groups(payload: dict) -> dict:
    """分组级兜底：某一组本轮全空时，沿用上一版该组数据。"""
    prev = merge_with_previous(payload, OUT, ["groups"])
    prev_map = {g.get("market"): g for g in prev.get("groups", [])}
    for g in payload.get("groups", []):
        if not g.get("items") and g.get("market") in prev_map:
            pg = prev_map[g["market"]]
            if pg.get("items"):
                log.warning("分组 %s 本轮无数据，沿用上一版", g["market"])
                g["items"] = pg["items"]
                g["currency"] = pg.get("currency", g.get("currency"))
    payload["groups"] = [g for g in payload.get("groups", []) if g.get("items")]
    return payload


def main() -> int:
    payload = build()
    if not payload:
        return 1
    payload = merge_groups(payload)
    path = write_json(OUT, payload)
    total = sum(len(g["items"]) for g in payload["groups"])
    log.info("已写入 %s（%d 组 / %d 个标的，来源 %s）", path.name, len(payload["groups"]), total, payload["source"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
