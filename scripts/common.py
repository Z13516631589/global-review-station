"""
公共工具：JSON 原子写入、日志、北京时间、可选依赖探测。
所有抓取脚本都依赖这里，保证「任何一个数据源挂掉都不会让站点构建失败」。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "src" / "data"
CN_TZ = timezone(timedelta(hours=8))

# 模块级 logger，供未调用 setup_logging 的工具函数使用
log = logging.getLogger("fetch.common")


def _install_http_timeout(seconds: float) -> None:
    """
    给所有 HTTP 请求加兜底超时。

    akshare 的很多接口调用 requests 时并不传 timeout，一旦数据源握手之后不响应，
    进程就会一直挂着 —— CI 里表现为「抓取数据」步骤卡十几分钟，整条流水线停摆，
    站点内容自然一整天不变。

    这里同时兜两条路：
      1. socket.setdefaulttimeout：pandas.read_csv / urllib 这类直连调用
      2. 给 requests.Session.request 注入默认 timeout：requests 系调用
    """
    import socket

    socket.setdefaulttimeout(seconds)
    try:
        import requests

        original = requests.Session.request
        if getattr(original, "_grs_timeout_patched", False):
            return

        def patched(self, *args, **kwargs):  # noqa: ANN001, ANN002
            kwargs.setdefault("timeout", seconds)
            return original(self, *args, **kwargs)

        patched._grs_timeout_patched = True  # type: ignore[attr-defined]
        requests.Session.request = patched
    except Exception:  # noqa: BLE001
        # requests 没装就无所谓，socket 层已经兜住了
        pass


_install_http_timeout(float(os.getenv("HTTP_TIMEOUT", "25")))


def setup_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format=f"[%(asctime)s][{name}][%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger(name)


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def now_iso() -> str:
    return now_cn().isoformat(timespec="seconds")


def read_json(filename: str) -> dict:
    """读取现有 JSON；不存在或损坏时返回空 dict。"""
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(filename: str, payload: dict) -> Path:
    """原子写入：先写临时文件再替换，避免构建过程中读到半截 JSON。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_name, path)
    return path


def merge_with_previous(payload: dict, filename: str, keys: list[str]) -> dict:
    """
    抓取部分失败时，用上一版 JSON 的对应字段兜底，
    避免「某个接口挂了 → 整页空白」。
    """
    prev = read_json(filename)
    for k in keys:
        if not payload.get(k) and prev.get(k):
            payload[k] = prev[k]
    return payload


def batch_from_args(default: str = "A股收盘") -> str:
    for i, a in enumerate(sys.argv):
        if a == "--batch" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--batch="):
            return a.split("=", 1)[1]
    return os.getenv("FETCH_BATCH", default)


def optional_import(module: str):
    """导入可选依赖，失败返回 None，不抛异常。"""
    try:
        return __import__(module)
    except Exception:
        return None


def retry_call(fn, *args, times: int = 4, delay: float = 1.0, label: str = "", **kwargs):
    """
    带重试的调用：本地代理 / 数据源偶发抖动时自动重试。
    全部失败返回 None，由调用方决定是否回退到上一版数据。
    """
    last_err = ""
    for i in range(times):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:120]
            if label:
                log.debug("%s 第 %d 次失败：%s", label, i + 1, last_err)
            time.sleep(delay * (i + 1))
    if label:
        log.warning("%s 重试 %d 次仍失败：%s", label, times, last_err)
    return None


def to_float(v, default=None):
    try:
        if v is None or v == "" or v == "-":
            return default
        return float(v)
    except Exception:
        return default


def clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
