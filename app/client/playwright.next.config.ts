import {defineConfig} from '@playwright/test'
import base from './playwright.config'
export default defineConfig({...base,testDir:'../tests/next',timeout:180000,use:{...base.use,actionTimeout:15000}})
