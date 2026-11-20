/**
 * 「我是谁」的本地选择：设置页写入，聊天发送时读取，作为消息的 personaId。
 *
 * 为什么放在 localStorage 而不是聊天表：酒馆 2.0 的 persona 只在「发消息」时
 * 随 payload 传给服务端（`MessageInput.personaId`），服务端不存聊天级人设。
 * 所以这里只保存「我用哪个人设」，对所有对话生效；换掉/删掉后下一轮即变。
 */
const STORAGE_KEY = 'tavern:persona'

/** 读取当前选择；隐私模式或没有 window 时返回 null。 */
export function readPersonaId(): string | null {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return null
    return window.localStorage.getItem(STORAGE_KEY) || null
  } catch {
    return null
  }
}

/** 保存选择；传 null / 空串表示不使用人设。 */
export function writePersonaId(id: string | null): void {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    if (id) window.localStorage.setItem(STORAGE_KEY, id)
    else window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* 存不了就只在本次会话里生效，不影响聊天 */
  }
}
