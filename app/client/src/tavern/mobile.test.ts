import {describe,expect,it} from 'vitest'
import {keyboardBottomOffset} from './mobile'

describe('keyboardBottomOffset', () => {
  it('returns zero when the visual viewport matches the layout viewport', () => {
    expect(keyboardBottomOffset(800, 800)).toBe(0)
    expect(keyboardBottomOffset(800, 800, 34)).toBe(0)
  })

  it('ignores small fluctuations below the threshold', () => {
    expect(keyboardBottomOffset(800, 750)).toBe(0)
    expect(keyboardBottomOffset(800, 730)).toBe(0)
  })

  it('returns the inset height when the keyboard opens', () => {
    expect(keyboardBottomOffset(800, 500)).toBe(300)
    expect(keyboardBottomOffset(800, 500, 34)).toBe(266)
  })

  it('caps absurd offsets and tolerates bad input', () => {
    expect(keyboardBottomOffset(800, 100)).toBe(560)
    expect(keyboardBottomOffset(Number.NaN, 500)).toBe(0)
    expect(keyboardBottomOffset(0, 0)).toBe(0)
    expect(keyboardBottomOffset(800, 500, -10)).toBe(300)
  })
})
