import {defineConfig} from '@playwright/test'
import base from './playwright.config'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/tests')
export default defineConfig({...base,testDir:'../tests/acceptance',timeout:120000,
  outputDir:path.join(artifacts,'acceptance-playwright'),
  reporter:[['list'],['json',{outputFile:path.join(artifacts,'acceptance-results.json')}]]})
