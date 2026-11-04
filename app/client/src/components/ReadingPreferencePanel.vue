<script setup lang="ts">
import {computed} from 'vue'
import {RotateCcw,Type} from 'lucide-vue-next'
import {DEFAULT_READING_PREFERENCE,readingPreference,readingStyle,resetReadingPreference,updateReadingPreference} from '../reader/reading-preference'
const preference=readingPreference
const fontLabel=computed(()=>preference.value.fontFamily==='serif'?'衬线':'无衬线')
function changeFontSize(event:Event){updateReadingPreference({fontSize:Number((event.target as HTMLInputElement).value)})}
function changeLineHeight(event:Event){updateReadingPreference({lineHeight:Number((event.target as HTMLInputElement).value)})}
function changeFontFamily(event:Event){updateReadingPreference({fontFamily:(event.target as HTMLSelectElement).value==='serif'?'serif':'sans'})}
function reset(){resetReadingPreference()}
</script>
<template>
  <section class="reading-preference" aria-label="正文阅读感设置">
    <div class="reading-preference-title"><Type :size="16"/><strong>正文阅读感</strong><small>只保存在这台设备</small></div>
    <div class="reading-preference-grid">
      <label>字号 · {{preference.fontSize}}px<input aria-label="正文字号" type="range" min="14" max="26" step="1" :value="preference.fontSize" @input="changeFontSize"></label>
      <label>行高 · {{preference.lineHeight.toFixed(2)}}<input aria-label="正文行高" type="range" min="1.4" max="2.4" step="0.05" :value="preference.lineHeight" @input="changeLineHeight"></label>
      <label>字体<select aria-label="正文字体" :value="preference.fontFamily" @change="changeFontFamily"><option value="sans">无衬线 · 屏幕阅读</option><option value="serif">衬线 · 书页感</option></select></label>
    </div>
    <div class="reading-preference-foot"><small>当前：{{preference.fontSize}}px / 行高 {{preference.lineHeight.toFixed(2)}} / {{fontLabel}}</small><button type="button" @click="reset"><RotateCcw :size="14"/>恢复默认</button></div>
    <p class="reading-preference-sample" :style="readingStyle">雾里的灯，终于有了回音。</p>
    <small class="product-hint">默认 {{DEFAULT_READING_PREFERENCE.fontSize}}px · 行高 {{DEFAULT_READING_PREFERENCE.lineHeight}}；阅读页与聊天正文会使用这里的设置。</small>
  </section>
</template>
<style scoped>
.reading-preference{display:grid;gap:12px;padding:16px;border:1px solid var(--edge);border-radius:14px;background:var(--paper)}
.reading-preference-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.reading-preference-title small{color:var(--quiet);font-size:12px}
.reading-preference-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:end}
.reading-preference-grid label{display:grid;gap:6px;font-size:13px;color:var(--quiet);margin:0}
.reading-preference-grid input[type=range]{width:100%;accent-color:var(--accent)}
.reading-preference-grid select{min-height:40px}
.reading-preference-foot{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.reading-preference-foot button{display:inline-flex;align-items:center;gap:6px;min-height:36px;padding:4px 10px;font-size:12px}
.reading-preference-sample{margin:0;padding:12px 14px;border:1px dashed var(--edge);border-radius:10px;background:var(--wash);overflow-wrap:anywhere}
@media(max-width:767px){.reading-preference-grid{grid-template-columns:1fr}}
</style>
