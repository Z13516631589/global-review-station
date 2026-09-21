/**
 * 数据加载层
 * ------------------------------------------------------------------
 * src/data/*.json 由 Python 脚本（scripts/fetch_*.py）生成，
 * 仓库内保留一份示例种子数据，保证任何情况下站点都能构建成功。
 */

import globalRaw from '../data/global_snapshot.json';
import ashareRaw from '../data/ashare.json';
import dragonsRaw from '../data/dragons.json';
import newsRaw from '../data/news.json';

export interface Quote {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
  asOf?: string;
}

export interface QuoteGroup {
  market: string;
  currency?: string;
  items: Quote[];
}

export interface GlobalSnapshot {
  updatedAt: string;
  batch: string;
  source?: string;
  stale?: boolean;
  note?: string;
  groups: QuoteGroup[];
}

export interface IndexRow {
  code: string;
  name: string;
  close: number;
  change: number;
  changePct: number;
  amount?: number;
  amplitude?: number;
}

export interface SectorRow {
  name: string;
  changePct: number;
  direction: 'up' | 'down' | 'flat';
  driver?: string;
  leading?: string[];
}

export interface Ashare {
  updatedAt: string;
  batch: string;
  source?: string;
  stale?: boolean;
  note?: string;
  indices: IndexRow[];
  breadth: {
    up: number;
    down: number;
    flat: number;
    limitUp: number;
    limitDown: number;
    total?: number;
  };
  volume: { amount: number; prevAmount: number; changePct: number };
  margin?: { balance: number; change: number };
  sentiment: {
    score: number;
    label: string;
    desc?: string;
    drivers?: string[];
  };
  sectors: SectorRow[];
  fundFlow?: Record<string, { net: number; unit?: string; note?: string }>;
}

export interface DragonRow {
  code: string;
  name: string;
  close: number;
  changePct: number;
  netBuy: number;
  buyAmount?: number;
  sellAmount?: number;
  reason?: string;
}

export interface Dragons {
  updatedAt: string;
  batch: string;
  source?: string;
  stale?: boolean;
  note?: string;
  items: DragonRow[];
  topInflow?: { code: string; name: string; changePct: number; mainNet: number }[];
  topOutflow?: { code: string; name: string; changePct: number; mainNet: number }[];
}

export type Sentiment = '利好' | '利空' | '中性';

export interface NewsItem {
  id: string;
  title: string;
  source: string;
  time: string;
  url?: string;
  sentiment: Sentiment;
  sector: string;
  analysis: string;
}

export interface News {
  updatedAt: string;
  batch: string;
  source?: string;
  analyzer?: string;
  stale?: boolean;
  note?: string;
  items: NewsItem[];
}

export const globalSnapshot = globalRaw as unknown as GlobalSnapshot;
export const ashare = ashareRaw as unknown as Ashare;
export const dragons = dragonsRaw as unknown as Dragons;
export const news = newsRaw as unknown as News;
