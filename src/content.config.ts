import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const pickSchema = z.object({
  code: z.string(),
  name: z.string(),
  buy: z.string().default('—'),
  stop: z.string().default('—'),
  position: z.string().default('—'),
  logic: z.string().default(''),
  status: z.string().default('观察中'),
});

const recordSchema = z.object({
  total: z.number().default(0),
  win: z.number().default(0),
  loss: z.number().default(0),
  avgReturn: z.number().default(0),
  note: z.string().default(''),
});

const reviews = defineCollection({
  loader: glob({ pattern: '**/*.{md,mdx}', base: './src/content/reviews' }),
  schema: z.object({
    title: z.string(),
    date: z.coerce.date(),
    author: z.string().default('复盘小组'),
    summary: z.string().default(''),
    // 数据批次：A股收盘 / 美股收盘 / 盘前
    batch: z.string().default('A股收盘'),
    dataUpdatedAt: z.string().optional(),
    // true 时会打「示例」标记，提示内容为模板数据
    sample: z.boolean().default(false),
    // 由 scripts/gen_review.py 自动生成时为 true；engine 记录观点来源：llm（大模型）/ rules（降级留空）
    generated: z.boolean().default(false),
    engine: z.string().optional(),
    tags: z.array(z.string()).default([]),
    // 明日预案：条件分支（客观描述，不构成建议）
    plan: z
      .array(
        z.object({
          condition: z.string(),
          action: z.string(),
        }),
      )
      .default([]),
    picks: z.array(pickSchema).default([]),
    record: recordSchema.optional(),
  }),
});

export const collections = { reviews };
