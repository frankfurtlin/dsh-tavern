import type {ProviderConfiguration} from '../domain/local-contract'
const options={enabled:true,slots:['text'] as ProviderConfiguration['slots'],contextLimit:32768,outputLimit:2048,dailyCalls:20,firstPacketMs:60000,idleMs:45000,totalMs:300000,verifiedAt:'2026-10-05'}
/** Registry metadata references official protocols. Configured limits are user defaults, not advertised model capabilities. */
export const TEXT_PRESETS:ProviderConfiguration[]=[
  {...options,id:'opencode-go-mimo',name:'OpenCode Go · MiMo 2.6 Flash',protocol:'chat-completions',baseUrl:'https://opencode.ai/zen/go/v1',modelId:'mimo-v2.6-flash',key:'',includeUsage:true,source:'https://opencode.ai/docs/go/'},
  {...options,id:'openai',name:'OpenAI',protocol:'responses',baseUrl:'https://api.openai.com/v1',modelId:'',key:'',source:'https://developers.openai.com/api/reference/resources/responses/streaming-events'},
  {...options,id:'anthropic',name:'Anthropic',protocol:'anthropic',baseUrl:'https://api.anthropic.com/v1',modelId:'',key:'',source:'https://docs.anthropic.com/en/api/messages-streaming'},
  {...options,id:'gemini',name:'Google Gemini',protocol:'gemini',baseUrl:'https://generativelanguage.googleapis.com/v1beta',modelId:'',key:'',source:'https://ai.google.dev/api/generate-content#method:-models.streamgeneratecontent'},
  {...options,id:'custom',name:'自定义 / 本机模型',protocol:'chat-completions',baseUrl:'http://127.0.0.1:11434/v1',modelId:'',key:'',source:'user-configured'},
]
