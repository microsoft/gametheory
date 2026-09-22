import type { ConfigurationContent } from './ConfigurationEditor'

function safeEndpoint(value: string) {
  if (!value) return true
  try {
    const url = new URL(value)
    return (
      (url.protocol === 'https:' ||
        (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      !/[\\%]/.test(value) &&
      !/(?:^|\/)\.\.?(?:\/|$)/.test(value)
    )
  } catch {
    return false
  }
}

export function configurationErrors(content: ConfigurationContent, kind: string): string[] {
  const errors: string[] = []
  for (const key of ['resource_id', 'identity_ref'] as const) {
    if (content[key] && !/^[A-Za-z0-9_./:@()+-]+$/.test(content[key]))
      errors.push(
        `${key === 'resource_id' ? 'Resource identity' : 'Identity reference'} must be a non-secret reference, not credentials or a connection string.`,
      )
  }
  if (kind === 'sql') {
    if (
      content.endpoint &&
      !/^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|[0-9a-fA-F:]+)$/.test(content.endpoint)
    )
      errors.push(
        'SQL endpoint metadata must be a hostname or IP address, not a query or connection string.',
      )
    if (content.database && !/^[A-Za-z0-9_. -]+$/.test(content.database))
      errors.push('Database metadata must be a database name, not a connection string.')
  } else {
    if (!safeEndpoint(content.endpoint))
      errors.push(
        'Use an HTTPS endpoint or explicit loopback HTTP, without credentials, query parameters, fragments, or traversal.',
      )
    if (content.database) errors.push('Database metadata applies only to SQL connections.')
  }
  if (content.notification) {
    const notification = content.notification
    const recipients = notification.recipients ?? []
    const mailboxes = [...recipients, ...(notification.sender ? [notification.sender] : [])]
    if (
      mailboxes.some(
        (address) =>
          address.includes('*') ||
          !/^[A-Za-z0-9.!#$&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$/.test(
            address,
          ) ||
          address.length > 254,
      )
    )
      errors.push(
        'Use one explicit mailbox per entry, without display names, wildcards, or headers.',
      )
    if (
      recipients.length > 100 ||
      new Set(recipients.map((value) => value.toLowerCase())).size !== recipients.length
    )
      errors.push('Supply at most 100 unique recipient mailboxes.')
    if (!safeEndpoint(notification.trusted_link))
      errors.push(
        'The trusted link must be HTTPS or explicit loopback HTTP, without credentials or query parameters.',
      )
  }
  return errors
}
