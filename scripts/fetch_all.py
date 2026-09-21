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
SCRIPTS = ["fetch_global.py", "fetch_ashare.py", "fetch_news.py"]


def run(name: str, extra: list[str]) -> int:
    print(f"\n=== {name} ===", flush=True)
    proc = subprocess.run(
        [sys.executable, str(HERE / name), *extra],
        cwd=str(HERE),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return proc.returncode


def main() -> int:
    extra = sys.argv[1:]
    codes = [run(s, extra) for s in SCRIPTS]
    failed = [s for s, c in zip(SCRIPTS, codes) if c != 0]
    print("\n=== 汇总 ===", flush=True)
    for s, c in zip(SCRIPTS, codes):
        print(f"  {s}: {'成功' if c == 0 else '失败（已保留上一版数据）'}")
    if failed:
        print("\n提示：失败通常是缺少依赖或数据源不可用。")
        print("      pip install -r scripts/requirements.txt")
    # 即使部分失败也返回 0，避免 CI 因单一数据源抖动而红
    return 0


if __name__ == "__main__":
    sys.exit(main())
