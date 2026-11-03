import {defineConfig} from '@playwright/test'
import {existsSync} from 'node:fs'
import path from 'node:path'
const channel=process.env.PLAYWRIGHT_CHANNEL||(process.platform==='win32'&&existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe')?'chrome':undefined)
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
export default defineConfig({testDir:'../tests/e2e',fullyParallel:false,workers:1,timeout:60000,use:{channel,baseURL:process.env.E2E_BASE_URL||'http://127.0.0.1:8811',trace:'retain-on-failure',screenshot:'only-on-failure'},outputDir:path.join(artifacts,'playwright'),reporter:[['list'],['json',{outputFile:path.join(artifacts,'playwright-results.json')}]]})
