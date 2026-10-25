import { ApiError } from './errors.ts'
import type { Pagination, Page } from './types.ts'

export function paginate<T, K extends string = 'records'>(rows: T[], body: Pagination, key: K = 'records' as K) {
  const page = body.pageNum ?? 1,
    size = body.pageSize ?? 10
  return { [key]: rows.slice((page - 1) * size, page * size), total: rows.length, current: page, size } as Page<T, K>
}
export function timeRange(start?: string, end?: string) {
  const parse = (value: string | undefined, fallback: number) => {
    if (!value) return fallback
    const time = Date.parse(value)
    if (!Number.isFinite(time)) throw new ApiError('时间格式无效')
    return time
  }
  const low = parse(start, -Infinity),
    high = parse(end, Infinity)
  if (low > high) throw new ApiError('开始时间不能晚于结束时间')
  return (value: string) => {
    const time = Date.parse(value)
    return time >= low && time <= high
  }
}
