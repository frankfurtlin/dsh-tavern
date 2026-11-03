import {readFileSync,writeFileSync} from 'node:fs'
import {fileURLToPath} from 'node:url'
const path=fileURLToPath(new URL('../ios/App/CapApp-SPM/Package.swift',import.meta.url))
writeFileSync(path,readFileSync(path,'utf8').replaceAll('\\','/'))
