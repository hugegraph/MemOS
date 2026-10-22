// Real parser and unchanged production UI, isolated JSONL missing input/output.
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { performanceCase } from './performance.mjs'
const output = process.env.ISSUE_REPORT_DIR
if (!output) throw new Error('Explicit report directory required')
await mkdir(output, { recursive: true })
let report
try {
  const evidence = await performanceCase(output, { missingFields: true })
  if (!evidence.missingFieldsDetail) throw new Error('Detail evidence missing')
  report = { status: 'PASS', scope: 'isolated synthetic JSONL; actual BFF and original production DOM', ...evidence }
} catch (error) {
  report = { status: 'FAIL', reason: error.message }
}
await writeFile(join(output, 'missing-fields.json'), JSON.stringify(report, null, 2))
console.log(JSON.stringify(report))
process.exitCode = report.status === 'PASS' ? 0 : 1
