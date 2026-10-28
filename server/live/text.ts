/** Bound UTF-16 length without splitting an extended grapheme (including ZWJ emoji). */
export function truncateGraphemes(text: string, limit: number): string {
  if (text.length <= limit) return text
  let end = 0
  for (const part of new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text)) {
    if (part.index + part.segment.length > limit) break
    end = part.index + part.segment.length
  }
  return text.slice(0, end) + '…'
}
