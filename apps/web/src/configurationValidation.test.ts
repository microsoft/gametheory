import { describe, expect, it } from 'vitest'
import { emptyConfiguration } from './ConfigurationEditor'
import { configurationErrors } from './configurationValidation'

describe('target metadata boundaries', () => {
  it('represents empty inputs as unresolved, without invented resources', () => {
    expect(configurationErrors(emptyConfiguration('rest'), 'rest')).toEqual([])
  })
  it.each([
    'https://user:password@example.test',
    'https://example.test?token=credential',
    'https://example.test/#fragment',
    'http://remote.example.test',
    'https://example.test/path/../private',
  ])('rejects credential-bearing and unrestricted endpoints (%s)', (endpoint) => {
    expect(configurationErrors({ ...emptyConfiguration('rest'), endpoint }, 'rest')).not.toEqual([])
  })
  it('allows only explicit loopback HTTP for local target metadata', () => {
    expect(
      configurationErrors(
        { ...emptyConfiguration('rest'), endpoint: 'http://127.0.0.1:9050' },
        'rest',
      ),
    ).toEqual([])
  })
  it('rejects connection strings in SQL metadata and non-SQL databases', () => {
    expect(
      configurationErrors(
        { ...emptyConfiguration('sql'), endpoint: 'Server=host;Password=credential;' },
        'sql',
      ),
    ).not.toEqual([])
    expect(
      configurationErrors({ ...emptyConfiguration('rest'), database: 'not-applicable' }, 'rest'),
    ).not.toEqual([])
  })
  it('requires unique explicit notification mailboxes', () => {
    const input = {
      ...emptyConfiguration('graph'),
      notification: {
        template_asset_id: null,
        sender: 'sender@example.test',
        recipients: ['person@example.test', 'PERSON@example.test'],
        trusted_link: '',
      },
    }
    expect(configurationErrors(input, 'graph').join(' ')).toContain('unique')
  })
})
