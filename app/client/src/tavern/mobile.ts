// 软键盘适配：visualViewport 高度小于布局视口时，差额即键盘遮挡高度。
// 纯函数便于单测；过小的抖动（<80px）忽略，上限 70% 视口，避免横屏/工具栏误判。
const MIN_KEYBOARD_PX = 80
const MAX_KEYBOARD_RATIO = 0.7

export function keyboardBottomOffset(
  layoutHeight: number,
  viewportHeight: number,
  insetBottom = 0,
): number {
  if (!Number.isFinite(layoutHeight) || !Number.isFinite(viewportHeight)) return 0
  if (layoutHeight <= 0 || viewportHeight <= 0) return 0
  const safeInset = Number.isFinite(insetBottom) && insetBottom > 0 ? insetBottom : 0
  const offset = Math.round(layoutHeight - viewportHeight - safeInset)
  if (offset < MIN_KEYBOARD_PX) return 0
  return Math.min(offset, Math.round(layoutHeight * MAX_KEYBOARD_RATIO))
}
