import type {PlayDefinition,PlayAction} from './contract'
const action=(id:string,label:string,to:string,cost=1,items:Record<string,number>={},flags:Record<string,boolean>={},needs:Record<string,number>={},known:Record<string,boolean>={}):PlayAction=>({id,label,to,cost,requires:{items:needs,flags:known},effects:{items,flags}})
export const playableTemplates:PlayDefinition[]=[
 {schema:'playable-definition-v1',name:'雾港最后一封信',description:'在雾散之前，把一封旧信送到北岸。',layout:'story',start:'gate',items:{key:{label:'铜钥匙',initial:0},letter:{label:'旧信',initial:1}},flags:{searched:{label:'看过门边的木匣',initial:false}},scenes:[
  {id:'gate',title:'渡口的门',text:'雾遮住了渡口。你怀里的信封写着北岸灯塔，木门却落着锁。\n\n门边有一只旧木匣，一线铜色从缝隙里透出来。',outcome:'continue',actions:[action('search','打开木匣','gate',2,{key:1},{searched:true},{},{searched:false}),action('unlock','用铜钥匙开门','pier',1,{key:-1},{},{key:1}),action('wait','等一阵风','gate',3)]},
  {id:'pier',title:'北岸',text:'钥匙转了半圈，门开了。小船靠上栈桥，灯塔下有人等你。\n\n“你带来了那封信吗？”她问。',outcome:'continue',actions:[action('deliver','把信交给她','done',1,{letter:-1},{},{letter:1}),action('return','回渡口看看','gate',4)]},
  {id:'done',title:'信已送达',text:'她读完信，往灯塔里添了一盏灯。雾里终于有了归航的方向。\n\n你把空下来的手收进衣袋，沿着晨光回家。',outcome:'success',actions:[]}
 ]},
 {schema:'playable-definition-v1',name:'灯塔守夜',description:'有限的电池、三次检查，一盏不能熄灭的灯。',layout:'dialogue',start:'room',items:{battery:{label:'备用电池',initial:2}},flags:{checked:{label:'查明断路处',initial:false},lit:{label:'灯塔已点亮',initial:false}},scenes:[
  {id:'room',title:'值班室',text:'无线电里传来船长的声音：“再过十分钟，我们就到礁石区。”\n\n灯塔没有亮。桌上只有两枚备用电池。',outcome:'continue',actions:[action('inspect','检查配电箱','room',2,{}, {checked:true},{},{checked:false}),action('repair','更换断路电池','balcony',3,{battery:-1},{lit:true},{battery:1},{checked:true}),action('leave','放弃守夜，离开灯塔','dark',1)]},
  {id:'balcony',title:'信号台',text:'灯光扫过海面。船长还没有看见入口，你需要再送出一组信号。',outcome:'continue',actions:[action('signal','启动备用信号灯','safe',2,{battery:-1},{},{battery:1},{lit:true}),action('look','观察远处的船','balcony',1)]},
  {id:'safe',title:'平安入港',text:'“收到。谢谢你，守灯人。”\n\n无线电归于平静。最后一艘船越过礁石，稳稳驶进港湾。',outcome:'success',actions:[]},
  {id:'dark',title:'无人守灯',text:'你沿台阶下山。身后没有灯光，只有风声。\n\n这次守夜没有完成。你仍可以从检查点重新选择。',outcome:'failure',actions:[]}
 ]},
 {schema:'playable-definition-v1',name:'旧书店的谜匣',description:'从三条线索里找出答案，打开书店的最后一只匣子。',layout:'investigation',start:'shop',items:{token:{label:'木刻书签',initial:0}},flags:{letter:{label:'读过店主的便笺',initial:false},clock:{label:'观察过停摆的钟',initial:false}},scenes:[
  {id:'shop',title:'打烊以后',text:'店主留下了一张便笺：“答案不在最亮的地方，在时间停住的地方。”\n\n桌上是一只带锁的谜匣。窗边挂钟停在七点，书架上夹着木刻书签。',outcome:'continue',actions:[action('letter','读便笺背面','shop',1,{}, {letter:true},{},{letter:false}),action('clock','看看停摆的钟','shop',1,{}, {clock:true},{},{clock:false}),action('token','取下木刻书签','shop',1,{token:1},{},{},{letter:true,clock:true}),action('open','将书签放进七号锁孔','garden',1,{token:-1},{},{token:1}),action('guess','试试最亮的铜锁孔','wrong',1)]},
  {id:'garden',title:'一座纸上的花园',text:'锁扣轻轻弹开。匣中没有金银，只有店主手绘的花园和一封信。\n\n“愿你也找到可以慢慢生活的地方。”',outcome:'success',actions:[]},
  {id:'wrong',title:'锁扣合上',text:'铜锁孔没有转动。谜匣的机关扣住了。\n\n这一次没有解开，但刚才发现的线索依然保存在检查点里。',outcome:'failure',actions:[]}
 ]}
]
