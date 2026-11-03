import type { CapacitorConfig } from '@capacitor/cli'
import {KeyboardResize} from '@capacitor/keyboard'
const config: CapacitorConfig = {
  appId: process.env.NATIVE_APP_ID || 'app.storyreader.personal',
  appName: process.env.NATIVE_APP_NAME || '故事阅读',
  webDir: 'dist',
  loggingBehavior: 'none',
  android: { allowMixedContent: Boolean(process.env.VITE_NATIVE_DEV_SERVER) },
  ios: { contentInset: 'automatic' },
  // Android's edge-to-edge window needs explicit IME resizing; Body serves iOS.
  plugins: {Keyboard:{resize:KeyboardResize.Body,resizeOnFullScreen:true}}
}
export default config
