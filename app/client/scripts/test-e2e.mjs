import {spawnSync} from 'node:child_process'
import path from 'node:path'
const result=spawnSync(process.execPath,['node_modules/@playwright/test/cli.js','test',...process.argv.slice(2)],{stdio:'inherit',env:{...process.env,NODE_PATH:path.resolve('node_modules')}})
process.exit(result.status??1)
