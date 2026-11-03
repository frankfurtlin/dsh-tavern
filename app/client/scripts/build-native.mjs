import {spawnSync} from 'node:child_process'
import {mkdirSync,copyFileSync,cpSync,rmSync} from 'node:fs'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..')
const platform=process.argv[2]
if(!['android','ios'].includes(platform))throw new Error('Platform must be android or ios')
function run(command,args,cwd=root){const shell=process.platform==='win32'&&(command==='npm'||command.endsWith('.bat'));const result=spawnSync(command,args,{cwd,stdio:'inherit',shell});if(result.status!==0)process.exit(result.status||1)}
run('npm',['run',`native:sync:${platform}`])
const out=path.resolve(root,'../artifacts',platform);mkdirSync(out,{recursive:true})
if(platform==='android'){
  if(process.argv.includes('--docker'))run('docker',['run','--rm','-v',root+':/workspace','-v','story-reader-gradle:/root/.gradle','-v','story-reader-android-debug:/root/.android','-w','/workspace/android','ghcr.io/cirruslabs/android-sdk:36@sha256:f9b3ea9ed2b5fc9522adae82c7b4622ab7aa54207ef532c8e615a347dca08f31','bash','gradlew','assembleDebug','--no-daemon'])
  else run(process.platform==='win32'?'gradlew.bat':'./gradlew',['assembleDebug','--no-daemon'],path.join(root,'android'))
  copyFileSync(path.join(root,'android/app/build/outputs/apk/debug/app-debug.apk'),path.join(out,process.env.VITE_NATIVE_DEV_SERVER?'app-debug-emulator.apk':'app-debug.apk'))
}else if(platform==='ios'){
  if(process.platform!=='darwin'){console.error('iOS 实际构建需要 macOS 与 Xcode 26+。工程已保留，未验证编译。');process.exit(2)}
  run('xcodebuild',['-project','App/App.xcodeproj','-scheme','App','-configuration','Debug','-sdk','iphonesimulator','-derivedDataPath',out,'CODE_SIGNING_ALLOWED=NO','build'],path.join(root,'ios'))
  const app=path.resolve(out,'App.app')
  if(path.dirname(app)!==out)throw new Error('iOS artifact path escaped output directory')
  rmSync(app,{recursive:true,force:true})
  cpSync(path.join(out,'Build/Products/Debug-iphonesimulator/App.app'),app,{recursive:true,dereference:false})
  run('ditto',['-c','-k','--sequesterRsrc','--keepParent',app,path.join(out,'App.app.zip')])
}else process.exit(2)
