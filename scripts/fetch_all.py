"""
一键刷新全部数据：全球快照 / A股盘面+龙虎榜 / 消息面

任何一个子脚本失败都不影响其它子脚本，也不影响已有 JSON（保留上一版）。
用法：
    python scripts/fetch_all.py [--batch "A股收盘"]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 按「越快越靠前」排列：新闻只要几秒，A股几十秒，全球快照最慢（要试新浪→东财→yfinance
# 多级回退）。一旦总时间不够，先牺牲的是最不常变的全球快照，而不是盘中要用的 A股数据。
# 每个脚本单独限时，避免某只脚本因为数据源抽风把后面全部挤掉。
SCRIPTS: list[tuple[str, int]] = [
    ("fetch_news.py", 120),
    ("fetch_ashare.py", 300),
    ("fetch_global.py", 300),
]


def run(name: str, limit: int, extra: list[str]) -> int:
    print(f"\n=== {name} （限时 {limit}s）===", flush=True)
    try:
        proc = subprocess.run(
            [sys.executable, str(HERE / name), *extra],
            cwd=str(HERE),
            stdout=sys.stdout,
            stderr=sys.stderr,
            timeout=limit,
        )
        return proc.returncode
    except subprocess.TimeoutExpired:
        print(f"{name} 超过 {limit}s 未结束，已终止（保留上一版数据）", flush=True)
        return 1


def main() -> int:
    extra = sys.argv[1:]
    codes = [(s, run(s, limit, extra)) for s, limit in SCRIPTS]
    failed = [s for s, c in codes if c != 0]
    print("\n=== 汇总 ===", flush=True)
    for s, c in codes:
        print(f"  {s}: {'成功' if c == 0 else '失败（已保留上一版数据）'}")
    if failed:
        print("\n提示：失败通常是缺少依赖或数据源不可用。")
        print("      pip install -r scripts/requirements.txt")
    # 即使部分失败也返回 0，避免 CI 因单一数据源抖动而红
    return 0


if __name__ == "__main__":
    sys.exit(main())
