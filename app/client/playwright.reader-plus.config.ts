import {defineConfig} from '@playwright/test'
import base from './playwright.config'
import path from 'node:path'
const artifacts=process.env.TEST_ARTIFACTS_DIR||path.resolve('../artifacts/reader-plus')
export default defineConfig({...base,testDir:'../tests/reader-plus',timeout:90000,
  outputDir:path.join(artifacts,'playwright-reader-plus'),
  reporter:[['list'],['json',{outputFile:path.join(artifacts,'reader-plus-results.json')}]]})
