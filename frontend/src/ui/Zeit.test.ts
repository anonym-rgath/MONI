import { describe, expect, test } from 'vitest'
import { dauer, vor } from './Zeit'

describe('Zeit', () => {
  test.each([[5, '5 s'], [59, '59 s'], [60, '1 min'], [720, '12 min'], [3600, '1 Std.'], [7200, '2 Std.'], [86400, '1 Tag'], [3 * 86400, '3 Tage']])('%i s -> %s', (s, text) => {
    expect(dauer(s)).toBe(text)
  })
  test('vor, negative Werte (Uhrenabweichung) werden zu 0 s', () => {
    expect(vor(720)).toBe('vor 12 min')
    expect(vor(-5)).toBe('vor 0 s')
  })
})
