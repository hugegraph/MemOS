// No formatting/functional exemptions: verify exact bytes against the authorized upstream update.
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { baselineFile, verifyFrontend } from './frontend-baseline.ts'

const result = verifyFrontend(process.cwd(), JSON.parse(await readFile(baselineFile, 'utf8')))
await mkdir('.data/issue-acceptance', { recursive: true })
await writeFile(
  '.data/issue-acceptance/frontend-unchanged.json',
  JSON.stringify(
    {
      status: 'PASS',
      ...result,
      scope: 'Exact approved upstream frontend bytes and file inventory; no custom changes or exemptions',
    },
    null,
    2,
  ),
)
console.log(`PASS: frontend source unchanged from approved upstream ${result.baseline}; no exemptions`)
