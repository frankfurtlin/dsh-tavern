class ReaderPcmRecorder extends AudioWorkletProcessor {
  constructor(){super();this.samples=new Float32Array(4096);this.length=0;this.port.onmessage=()=>{if(this.length)this.port.postMessage(this.samples.slice(0,this.length));this.length=0;this.port.postMessage({flushed:true})}}
  process(inputs){const channel=inputs[0]?.[0];if(channel?.length)for(const sample of channel){this.samples[this.length++]=sample;if(this.length===4096){this.port.postMessage(this.samples);this.samples=new Float32Array(4096);this.length=0}}return true}
}
registerProcessor('reader-pcm-recorder',ReaderPcmRecorder)
