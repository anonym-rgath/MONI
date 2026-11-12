import { describe, expect, test } from 'vitest'
import { formatWert, labelText, METRIKEN } from './metriken'

describe('formatWert', () => {
  test.each([
    ['moni_host_cpu_usage_percent', 81.5, '81,5 %'],
    ['moni_host_temperature_celsius', 66, '66 °C'],
    ['moni_host_memory_total_bytes', 2147483648, '2,0 GB'],
    ['moni_filesystem_used_bytes', 512, '512 B'],
    ['moni_filesystem_used_bytes', 1536, '1,5 KB'],
    ['moni_filesystem_size_bytes', 3 * 1024 ** 4, '3,0 TB'],
    ['moni_host_uptime_seconds', 7200, '2 Std.'],
    ['moni_container_restarts', 1234, '1.234'],
    ['moni_host_load1', 0.52, '0,5'],
    ['moni_server_up', 0, 'nicht erreichbar'],
    ['moni_server_up', 1, 'erreichbar'],
    ['moni_container_up', 0, 'gestoppt'],
    ['moni_container_healthy', 0, 'Healthcheck schlägt fehl'],
    ['moni_docker_up', 0, 'nicht erreichbar'],
    ['moni_host_cpu_usage_percent', null, 'keine Daten'],
    ['moni_host_cpu_usage_percent', NaN, 'keine Daten'],
    ['unbekannte_metrik', 3, '3'],
  ])('%s %s -> %s', (m, w, text) => { expect(formatWert(m, w)).toBe(text) })

  test('bytes_je_s hängt /s an', () => {
    METRIKEN['t'] = { name: 't', einheit: 'bytes_je_s' }
    expect(formatWert('t', 2048)).toBe('2,0 KB/s')
    delete METRIKEN['t']
  })
})

test('labelText', () => {
  expect(labelText({ mountpoint: '/var' })).toBe('Einhängepunkt /var')
  expect(labelText({ name: 'photoprism' })).toBe('Container photoprism')
  expect(labelText({ zone: 'cpu', interface: 'eth0', foo: 'x' })).toBe('Zone cpu, Schnittstelle eth0, foo x')
  expect(labelText({})).toBe('')
})
