/**
 * npm run fetch 的跨平台包装器
 *
 * - 自动探测可用 Python（FETCH_PY 环境变量优先，其次 python / python3 / py）
 * - 转发参数给 scripts/ 下的 Python 脚本
 * - --ensure-deps 一键安装 Python 依赖（akshare / yfinance / pandas）
 *
 * 用法：
 *   npm run fetch                      刷新全部数据
 *   npm run fetch -- --batch "美股收盘"  指定更新批次
 *   npm run fetch:news                 只刷消息面
 *   npm run bootstrap:py               安装 Python 依赖
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');

function canRun(cmd) {
  const r = spawnSync(cmd, ['-c', 'import sys; print(sys.version)'], { shell: false });
  return r.status === 0;
}

function detectPython() {
  if (process.env.FETCH_PY) return process.env.FETCH_PY;
  for (const cmd of ['python', 'python3', 'py']) {
    if (canRun(cmd)) return cmd;
  }
  return null;
}

function run(cmd, args) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, { stdio: 'inherit', cwd: HERE, shell: false });
    p.on('close', (code) => resolve(code ?? 1));
    p.on('error', () => resolve(1));
  });
}

const argv = process.argv.slice(2);

if (argv.includes('--ensure-deps')) {
  const py = detectPython();
  if (!py) {
    console.error('[run-fetch] 未找到可用的 Python，请先安装 Python 3.10+');
    process.exit(1);
  }
  const code = await run(py, ['-m', 'pip', 'install', '-r', path.join(HERE, 'requirements.txt')]);
  process.exit(code);
}

const py = detectPython();
if (!py) {
  console.error('[run-fetch] 未找到 Python。请安装 Python 3.10+，或设置 FETCH_PY 指向可执行文件。');
  console.error('[run-fetch] 站点仍可正常构建，展示的是仓库内置的示例数据。');
  process.exit(1);
}

const known = new Set(['fetch_all.py', 'fetch_global.py', 'fetch_ashare.py', 'fetch_news.py']);
const scriptArg = argv.find((a) => known.has(a));
const script = scriptArg || 'fetch_all.py';
const passArgs = argv.filter((a) => a !== scriptArg);

const target = path.join(HERE, script);
if (!existsSync(target)) {
  console.error(`[run-fetch] 脚本不存在：${target}`);
  process.exit(1);
}

console.log(`[run-fetch] python=${py} script=${script} ${passArgs.join(' ')}`.trim());
const code = await run(py, [target, ...passArgs]);
if (code !== 0) {
  console.warn(`[run-fetch] ${script} 退出码 ${code}（已保留上一版数据，站点构建不受影响）`);
}
// 数据抓取失败不应让 npm run build 中断
process.exit(0);
