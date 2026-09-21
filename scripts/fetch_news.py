"""
消息面抓取 + 自动打标签 + 自动生成影响分析

流程：
  1. 抓取：akshare 财联社电报 / 全球财经快讯 / 财经早餐，外加可选 RSS
  2. 去重：标题指纹
  3. 分析：优先调用兼容 OpenAI 的 LLM（Gemini / DeepSeek / 通义等），
     失败或超配额自动回退规则词典 + 模板，保证任何情况下都有分析
  4. 输出：src/data/news.json

约束（写死在 prompt 里）：
  只做「事件 → 影响板块 / 方向」的事实性归纳，
  不输出买卖点位，不臆测涨跌结论。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from common import CN_TZ, batch_from_args, merge_with_previous, now_cn, now_iso, optional_import, retry_call, setup_logging, write_json

log = setup_logging("fetch_news")
OUT = "news.json"

MAX_ITEMS = int(os.getenv("NEWS_MAX_ITEMS", "40"))
LLM_MAX_CALLS = int(os.getenv("NEWS_LLM_MAX_CALLS", "20"))
# 只保留最近 N 天内的消息，过滤 RSS / 接口里混入的陈年旧文
MAX_AGE_DAYS = float(os.getenv("NEWS_MAX_AGE_DAYS", "3"))


def parse_time(raw: str):
    """把各种时间格式归一为北京时间；解析失败返回 None（该条直接丢弃）。"""
    s = str(raw or "").strip()
    if not s:
        return None
    # 纯数字时间戳（秒/毫秒）
    if s.isdigit():
        v = int(s)
        if v > 1e12:
            v //= 1000
        try:
            return datetime.fromtimestamp(v, CN_TZ)
        except Exception:  # noqa: BLE001
            return None
    # RFC822（RSS pubDate）
    try:
        from email.utils import parsedate_to_datetime

        d = parsedate_to_datetime(s)
        if d is not None:
            return d.astimezone(CN_TZ)
    except Exception:  # noqa: BLE001
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s[: len(datetime.now().strftime(fmt)) + 2][:19], fmt).replace(tzinfo=CN_TZ)
        except Exception:  # noqa: BLE001
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=CN_TZ)
    except Exception:  # noqa: BLE001
        return None


def fmt_time(d: datetime) -> str:
    return d.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M")

# --------------------------------------------------------------------------
# 规则兜底：关键词 → 板块 / 情绪
# --------------------------------------------------------------------------
SECTOR_KEYWORDS = {
    "农业": ["农业", "种业", "种植", "粮食", "养殖", "生猪", "一号文件", "转基因", "农产品"],
    "半导体": ["半导体", "芯片", "晶圆", "光刻", "国产替代", "集成电路", "存储", "封测"],
    "AI": ["人工智能", "大模型", "算力", "GPU", "AI", "数据中心", "英伟达", "算力租赁"],
    "新能源": ["新能源车", "电动车", "锂电", "电池", "充电桩", "销量", "渗透率"],
    "光伏": ["光伏", "硅片", "组件", "逆变器", "硅料", "产能", "检修"],
    "金融": ["券商", "银行", "保险", "两融", "融资保证金", "MLF", "LPR", "降准", "降息", "央行", "利息"],
    "地产": ["地产", "楼市", "购房", "房贷", "土地", "保交楼"],
    "医药": ["医药", "创新药", "集采", "医疗器械", "疫苗", "CXO"],
    "军工": ["军工", "国防", "装备", "导弹", "航空发动机"],
    "消费": ["消费", "白酒", "食品饮料", "零售", "餐饮", "旅游", "免税"],
    "商品": ["黄金", "原油", "铜", "铝", "煤炭", "钢铁", "大宗", "有色"],
    "中概": ["中概", "美股", "纳斯达克", "标普", "港股", "恒生", "阿里", "拼多多", "京东"],
    "宏观": ["宏观", "GDP", "PMI", "CPI", "PPI", "社融", "信贷", "财政", "税收", "外贸", "出口"],
}

POSITIVE_WORDS = [
    "利好", "上涨", "增长", "超预期", "创新高", "获批", "落地", "加码", "突破",
    "中标", "扩产", "提价", "涨价", "回暖", "复苏", "补贴", "降准", "降息", "净投放", "上修",
]
NEGATIVE_WORDS = [
    "利空", "下滑", "下降", "低于预期", "亏损", "减持", "处罚", "调查", "降价",
    "跌", "走弱", "疲软", "萎缩", "收紧", "违约", "退市", "检修", "限产", "下修", "回落",
]


def rule_analyze(title: str) -> dict:
    text = str(title)
    sector = "宏观"
    best_hit = 0
    for sec, kws in SECTOR_KEYWORDS.items():
        hit = sum(1 for kw in kws if kw in text)
        if hit > best_hit:
            best_hit, sector = hit, sec

    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    if pos > neg:
        sentiment = "利好"
    elif neg > pos:
        sentiment = "利空"
    else:
        sentiment = "中性"

    direction = {"利好": "偏正面", "利空": "偏负面", "中性": "中性"}[sentiment]
    analysis = f"该消息指向{sector}板块，事件本身对相关标的的影响方向{direction}，可观察其成交与价格反应是否延续。"
    return {"sentiment": sentiment, "sector": sector, "analysis": analysis}


# --------------------------------------------------------------------------
# 数据源 1：akshare
# --------------------------------------------------------------------------
def fetch_from_akshare(ak, limit: int):
    candidates = [
        ("stock_info_global_cls", {"symbol": "全部"}),
        ("stock_info_global_em", {}),
        ("stock_info_cjzc_em", {}),
        ("stock_news_em", {}),
    ]
    raw = []
    for name, kwargs in candidates:
        fn = getattr(ak, name, None)
        if fn is None:
            continue
        try:
            df = fn(**kwargs)
            if df is None or len(df) == 0:
                continue
            title_col = _find(df, ["标题", "内容", "摘要"])
            # 日期与时间可能分列（如财联社：发布日期 / 发布时间），合并后再解析
            date_col = _find(df, ["发布日期", "日期"])
            time_col = _find(df, ["发布时间", "时间"])
            src_col = _find(df, ["来源"])
            url_col = _find(df, ["链接", "url", "URL"])
            for _, row in df.head(limit).iterrows():
                title = str(row.get(title_col, "")).strip() if title_col else ""
                if not title:
                    continue
                parts = [str(row.get(c, "")).strip() for c in (date_col, time_col) if c]
                stamp = " ".join(p for p in parts if p and p.lower() != "nat")
                raw.append(
                    {
                        "title": title,
                        "source": str(row.get(src_col, "")).strip() if src_col else "akshare",
                        "time": stamp,
                        "url": str(row.get(url_col, "")).strip() if url_col else "",
                    }
                )
            log.info("akshare 接口 %s 取到 %d 条", name, len(raw))
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("akshare 接口 %s 失败：%s", name, exc)
    return raw


def _find(df, keywords):
    for kw in keywords:
        for c in df.columns:
            if kw in str(c):
                return c
    return None


# --------------------------------------------------------------------------
# 数据源 2：RSS（可选，逗号分隔，环境变量 NEWS_RSS）
# --------------------------------------------------------------------------
DEFAULT_RSS = "https://rss.sina.com.cn/finance/rollnews.xml"


def fetch_from_rss(limit: int):
    feeds = [s.strip() for s in os.getenv("NEWS_RSS", DEFAULT_RSS).split(",") if s.strip()]
    raw = []
    for url in feeds:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            nodes = root.findall(".//item") or root.findall(".//atom:entry", ns)
            for n in nodes[:limit]:
                title = (n.findtext("title") or "").strip()
                link = (n.findtext("link") or "").strip()
                pub = (n.findtext("pubDate") or n.findtext("{http://www.w3.org/2005/Atom}updated") or "").strip()
                if title:
                    raw.append({"title": title, "source": "RSS", "time": pub, "url": link})
            log.info("RSS %s 取到 %d 条", url, len(raw))
        except Exception as exc:  # noqa: BLE001
            log.warning("RSS %s 失败：%s", url, exc)
    return raw


def dedupe(items, limit):
    seen, out = set(), []
    for it in items:
        key = hashlib.md5(re.sub(r"\s+", "", it["title"]).encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# 分析：LLM 优先，规则兜底
# --------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "你是A股复盘助手，负责把财经消息整理成结构化记录。"
    "对每条消息输出且仅输出 JSON 数组中的一个对象，字段为："
    "sentiment(利好/利空/中性)、sector(受影响板块，2-4字)、analysis(一句话影响分析，30-45字)。"
    "严格要求：只做事实性影响归纳，即「事件 → 影响板块 / 方向」；"
    "不得给出买卖点位、不得给出仓位建议、不得臆测涨跌结论、不得使用夸张修辞。"
    "输出必须是合法 JSON 数组，不要任何解释文字。"
)


def llm_analyze_batch(titles: list[str]) -> list[dict] | None:
    api_key = os.getenv("LLM_API_KEY")
    base = os.getenv("LLM_API_BASE")
    model = os.getenv("LLM_MODEL", "gemini-2.0-flash")
    if not api_key or not base:
        return None

    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "请分析以下消息：\n"
                + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles)),
            },
        ],
    }
    url = base.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            return _parse_json_array(content, len(titles))
        except urllib.error.HTTPError as exc:
            log.warning("LLM HTTP %s（第 %d 次）", exc.code, attempt + 1)
            if exc.code in (401, 403, 404):
                break
            time.sleep(2 ** attempt)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM 调用失败（第 %d 次）：%s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return None


def _parse_json_array(text: str, expected: int):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start : end + 1])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(arr, list) or len(arr) != expected:
        return None
    out = []
    for obj in arr:
        s = str(obj.get("sentiment", "中性"))
        if s not in ("利好", "利空", "中性"):
            s = "中性"
        out.append(
            {
                "sentiment": s,
                "sector": str(obj.get("sector", "宏观"))[:8],
                "analysis": str(obj.get("analysis", "")).strip()[:120],
            }
        )
    return out


def analyze_all(items: list[dict]) -> tuple[list[dict], str]:
    """返回 (带分析的条目, 使用的引擎名)。"""
    titles = [i["title"] for i in items]
    results: list[dict | None] = [None] * len(items)
    engine = "rules"
    calls = 0

    if os.getenv("DISABLE_LLM", "").lower() in ("1", "true", "yes"):
        log.info("已通过 DISABLE_LLM 关闭大模型分析，直接使用规则兜底")
    else:
        size = 8
        for start in range(0, len(titles), size):
            if calls >= LLM_MAX_CALLS:
                log.warning("达到 NEWS_LLM_MAX_CALLS=%d 上限，剩余改用规则", LLM_MAX_CALLS)
                break
            chunk = titles[start : start + size]
            got = llm_analyze_batch(chunk)
            calls += 1
            if got is None:
                log.warning("LLM 批次失败，本批改用规则兜底")
                continue
            for offset, r in enumerate(got):
                results[start + offset] = r
            engine = "llm"

    for idx, item in enumerate(items):
        r = results[idx] or rule_analyze(item["title"])
        if not r.get("analysis"):
            r = rule_analyze(item["title"])
        item.update(r)
    return items, engine


# --------------------------------------------------------------------------
def main() -> int:
    ak = optional_import("akshare")
    raw = []
    if ak is not None:
        raw += fetch_from_akshare(ak, MAX_ITEMS)
    else:
        log.warning("未安装 akshare，跳过 akshare 新闻源")
    if len(raw) < MAX_ITEMS:
        raw += fetch_from_rss(MAX_ITEMS - len(raw))

    items = dedupe(raw, MAX_ITEMS * 3)

    # 只保留最近 N 天内的真实新闻：过滤 RSS / 接口混入的陈年旧文与无法解析的时间
    cutoff = now_cn() - timedelta(days=MAX_AGE_DAYS)
    kept = []
    for it in items:
        d = parse_time(it.get("time", ""))
        if d is None:
            continue
        if d < cutoff or d > now_cn() + timedelta(hours=1):
            continue
        it["time"] = fmt_time(d)
        kept.append(it)
    items = kept[:MAX_ITEMS]
    if not items:
        log.warning("近 %s 天内没有可用的新闻，保留上一版 JSON", MAX_AGE_DAYS)
        return 1

    for i, it in enumerate(items):
        it["id"] = hashlib.md5(it["title"].encode("utf-8")).hexdigest()[:12]
        it.setdefault("url", "")
        it.setdefault("source", "—")
        it["_i"] = i
    items.sort(key=lambda x: x["time"], reverse=True)

    items, engine = analyze_all(items)
    for it in items:
        it.pop("_i", None)

    payload = {
        "updatedAt": now_iso(),
        "batch": batch_from_args("A股收盘"),
        "source": "akshare+rss",
        "analyzer": engine,
        "stale": False,
        "note": "影响分析由大模型或规则词典自动生成，仅做事实性归纳，不构成投资建议。",
        "items": [
            {
                "id": it["id"],
                "title": it["title"],
                "source": it["source"],
                "time": it["time"],
                "url": it.get("url", ""),
                "sentiment": it["sentiment"],
                "sector": it["sector"],
                "analysis": it["analysis"],
            }
            for it in items
        ],
    }
    payload = merge_with_previous(payload, OUT, ["items"])
    write_json(OUT, payload)
    log.info("已写入 %s（%d 条，引擎 %s）", OUT, len(payload["items"]), engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
