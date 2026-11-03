export async function fieldImage(file:File):Promise<string>{
 if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>5*1024*1024)throw Error('请选择小于 5 MB 的 PNG、JPEG 或 WebP。')
 const bitmap=await createImageBitmap(file)
 try{
  if(bitmap.width<32||bitmap.height<32||bitmap.width*bitmap.height>16000000)throw Error('图片边长至少 32 像素，总像素不能超过 1600 万。')
  const scale=Math.min(1,384/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas')
  canvas.width=Math.max(32,Math.round(bitmap.width*scale));canvas.height=Math.max(32,Math.round(bitmap.height*scale))
  canvas.getContext('2d')!.drawImage(bitmap,0,0,canvas.width,canvas.height)
  const result=canvas.toDataURL('image/webp',.8);if(result.length>150000)throw Error('图片仍然过大，请换一张更简单的图片。');return result
 }finally{bitmap.close()}
}
