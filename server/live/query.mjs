import { ApiError } from './errors.mjs'

export function paginate(rows, body, key = 'records') {
  const page = body.pageNum ?? 1,
    size = body.pageSize ?? 10
  return { [key]: rows.slice((page - 1) * size, page * size), total: rows.length, current: page, size }
}
export function timeRange(start, end) {
  const parse = (value, fallback) => {
    if (!value) return fallback
    const time = Date.parse(value)
    if (!Number.isFinite(time)) throw new ApiError('时间格式无效')
    return time
  }
  const low = parse(start, -Infinity),
    high = parse(end, Infinity)
  if (low > high) throw new ApiError('开始时间不能晚于结束时间')
  return (value) => {
    const time = Date.parse(value)
    return time >= low && time <= high
  }
}
