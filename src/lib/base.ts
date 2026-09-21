/**
 * 部署路径助手
 *
 * GitHub Pages 的项目站地址是 https://用户名.github.io/仓库名/，
 * 所有内部链接都必须带上 /仓库名 前缀。
 * - 本地 / 根域名部署：BASE_URL = '/' → BASE = '' → u('/reviews') = '/reviews'
 * - Pages 项目站：   BASE_URL = '/global-review-station/' → BASE = '/global-review-station'
 */

export const BASE: string = ((import.meta.env.BASE_URL as string) || '/').replace(/\/+$/, '');

/** 给站内绝对路径加部署前缀，path 必须以 / 开头 */
export function u(path: string): string {
  return `${BASE}${path}`;
}

/** 判断当前路径是否高亮（给导航用） */
export function isActive(current: string, href: string): boolean {
  const cur = current.replace(/\/+$/, '');
  if (href === '/') return cur === BASE || cur === '';
  return cur.startsWith(u(href));
}
