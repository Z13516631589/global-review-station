import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import tailwind from '@astrojs/tailwind';

// 站点地址：部署到 Vercel / Cloudflare Pages 后改成实际域名，
// 本地开发与 GitHub Pages project 模式都可留空（Astro 会自动降级为相对路径）。
const SITE = process.env.SITE_URL || 'https://global-review-station.vercel.app';

export default defineConfig({
  site: SITE,
  base: process.env.BASE_PATH || '/',
  integrations: [mdx(), tailwind({ applyBaseStyles: false })],
  markdown: {
    shikiConfig: {
      theme: 'github-dark-dimmed',
      wrap: true,
    },
  },
  build: {
    // 复盘站为纯静态，输出目录 dist/
    format: 'directory',
  },
  vite: {
    build: {
      // 数据 JSON 会被 Python 脚本覆盖，禁止持久化缓存导致内容过期
      assetsInlineLimit: 0,
    },
  },
});
