/** YOLO 线路缺陷类别映射（单张/批量结果共用）。

 * 颜色对齐后端 tools/yolo_mcp/yolo_detect.py 的 CLASS_COLORS。
 */

/** 类别 id → 颜色（class_id 为数字 key） */
export const CLASS_COLOR: Record<string, string> = {
  0: '#dc2626', // broken 破损 → 红
  1: '#16a34a', // bird nest 鸟巢 → 绿
  2: '#f59e0b', // object 异物 → 橙
}

/** 类别英文名 → 中文 */
export const CLASS_ZH: Record<string, string> = {
  broken: '破损',
  'bird nest': '鸟巢',
  object: '异物',
}

/** 类别 id → 中文 */
const CLASS_ID_ZH: Record<number, string> = { 0: '破损', 1: '鸟巢', 2: '异物' }

/** 根据类别 id 取中文名 */
export function clsIdName(id: number): string {
  return CLASS_ID_ZH[id] ?? `类别${id}`
}

/** 根据检测项取颜色 */
export function clsColor(d: any): string {
  return CLASS_COLOR[String(d.class_id)] ?? '#2563eb'
}

/** 根据检测项取中文类别名 */
export function clsName(d: any): string {
  return d.class_name ? `${CLASS_ZH[d.class_name] ?? d.class_name}` : `类别${d.class_id}`
}
