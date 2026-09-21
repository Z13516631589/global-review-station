/** 数值 / 时间格式化工具（中国习惯：涨红跌绿） */

export function fmtNum(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(digits)}%`;
}

export function fmtSigned(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function dirClass(v: number | undefined | null): string {
  if (v === undefined || v === null || Number.isNaN(v) || v === 0) return 'text-flat';
  return v > 0 ? 'text-up' : 'text-down';
}

export function dirChip(v: number | undefined | null): string {
  if (v === undefined || v === null || Number.isNaN(v) || v === 0) return 'chip-flat';
  return v > 0 ? 'chip-up' : 'chip-down';
}

/** 万 → 亿元 */
export function wanToYi(v: number | undefined | null): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return (v / 10000).toFixed(2);
}

export function fmtDateTime(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

export function fmtDate(d: Date | string): string {
  const dt = typeof d === 'string' ? new Date(d) : d;
  if (Number.isNaN(dt.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
}

export function weekdayCN(d: Date | string): string {
  const dt = typeof d === 'string' ? new Date(d) : d;
  if (Number.isNaN(dt.getTime())) return '';
  return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][dt.getDay()];
}

/** 情绪分 → 颜色（低分=弱=绿，高分=强=红，遵循中国配色直觉） */
export function sentimentColor(score: number): string {
  if (score >= 70) return '#f2385a';
  if (score >= 55) return '#fb923c';
  if (score >= 45) return '#f5c451';
  if (score >= 30) return '#38bdf8';
  return '#12b981';
}
