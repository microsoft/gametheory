function object(value: unknown, context: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new Error(`${context} must be an object.`)
  return value as Record<string, unknown>
}

function onlyKeys(value: Record<string, unknown>, keys: string[], context: string) {
  const unexpected = Object.keys(value).find((key) => !keys.includes(key))
  if (unexpected) throw new Error(`${context}: unsupported field "${unexpected}".`)
}

function text(value: unknown, context: string, maximum = 4000): string {
  if (typeof value !== 'string' || !value.trim() || value.length > maximum)
    throw new Error(`${context} must be a nonempty string.`)
  return value
}

function list(value: unknown, context: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${context} must be an array.`)
  return value
}

function fields(value: unknown, context: string): Set<string> {
  const names = new Set<string>()
  const entries = list(value, context)
  if (entries.length > 100) throw new Error(`${context} can contain at most 100 fields.`)
  for (const raw of entries) {
    const field = object(raw, context)
    onlyKeys(
      field,
      ['name', 'type', 'required', 'minimum', 'maximum', 'max_length', 'choices'],
      context,
    )
    const name = text(field.name, `${context} name`)
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(name))
      throw new Error(`${context}: "${name}" is not a scalar field identifier.`)
    if (
      new Set([
        'password',
        'passwd',
        'secret',
        'clientsecret',
        'token',
        'accesstoken',
        'refreshtoken',
        'apikey',
        'authorization',
        'headers',
        'connectionstring',
      ]).has(name.toLowerCase().replace(/[^a-z]/g, ''))
    )
      throw new Error(`${context}: credentials and arbitrary headers are not accepted.`)
    if (names.has(name)) throw new Error(`${context}: duplicate field "${name}".`)
    names.add(name)
    if (
      !['string', 'integer', 'number', 'boolean', 'uuid', 'datetime'].includes(String(field.type))
    )
      throw new Error(`${context}: "${name}" has an unsupported scalar type.`)
    if (field.required !== undefined && typeof field.required !== 'boolean')
      throw new Error(`${context}: "${name}" required must be true or false.`)
    for (const key of ['minimum', 'maximum'] as const) {
      if (field[key] !== undefined && field[key] !== null) {
        if (
          typeof field[key] !== 'number' ||
          !Number.isFinite(field[key]) ||
          !['integer', 'number'].includes(String(field.type))
        )
          throw new Error(`${context}: "${name}" ${key} must be a finite numeric constraint.`)
      }
    }
    if (
      typeof field.minimum === 'number' &&
      typeof field.maximum === 'number' &&
      field.minimum > field.maximum
    )
      throw new Error(`${context}: "${name}" minimum exceeds maximum.`)
    if (
      field.max_length !== undefined &&
      field.max_length !== null &&
      (typeof field.max_length !== 'number' ||
        !Number.isSafeInteger(field.max_length) ||
        field.max_length < 1 ||
        field.max_length > 4000)
    )
      throw new Error(`${context}: "${name}" max_length must be a positive integer.`)
    if (
      field.choices !== undefined &&
      field.choices !== null &&
      (!Array.isArray(field.choices) ||
        field.choices.length < 1 ||
        field.choices.length > 100 ||
        !field.choices.every((choice) => typeof choice === 'string' && choice.length <= 4000) ||
        new Set(field.choices).size !== field.choices.length)
    )
      throw new Error(`${context}: "${name}" choices must be 1–100 unique bounded strings.`)
    if (field.type !== 'string' && (field.max_length != null || field.choices != null))
      throw new Error(`${context}: "${name}" length and choices constraints apply only to strings.`)
    if (
      typeof field.max_length === 'number' &&
      Array.isArray(field.choices) &&
      field.choices.some(
        (choice) => typeof choice === 'string' && choice.length > Number(field.max_length),
      )
    )
      throw new Error(`${context}: "${name}" choices exceed max_length.`)
  }
  return names
}

export function validateOperationCatalog(value: unknown, connectionKind: string): void {
  const catalog = object(value, 'Operation catalog')
  onlyKeys(catalog, ['schema_version', 'name', 'operations'], 'Operation catalog')
  if (catalog.schema_version !== 'operation-catalog/v1')
    throw new Error(
      'Choose a supported operation-catalog/v1 file, not an OpenAPI or scenario file.',
    )
  text(catalog.name, 'Catalog name', 160)
  const operations = list(catalog.operations, 'Catalog operations')
  if (operations.length > 200)
    throw new Error('An operation catalog can contain at most 200 operations.')
  const identities = new Set<string>()
  for (const raw of operations) {
    const operation = object(raw, 'Operation')
    onlyKeys(
      operation,
      ['key', 'version', 'label', 'effect', 'invocation', 'parameters', 'results', 'recovery'],
      'Operation',
    )
    const key = text(operation.key, 'Operation key')
    const version = text(operation.version, `Operation ${key} version`)
    if (
      !/^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(key) ||
      !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$/.test(version)
    )
      throw new Error('Operation keys and versions must be identifiers, not URLs or expressions.')
    const identity = JSON.stringify([key, version])
    if (identities.has(identity))
      throw new Error(`Duplicate operation "${key}" version "${version}".`)
    identities.add(identity)
    text(operation.label, `Operation ${key} label`, 160)
    text(operation.recovery, `Operation ${key} recovery description`)
    if (!['read', 'write', 'notify'].includes(String(operation.effect)))
      throw new Error(`Operation ${key} has an unsupported effect.`)
    const parameters = fields(operation.parameters, `Operation ${key} parameters`)
    fields(operation.results, `Operation ${key} results`)
    const invocation = object(operation.invocation, `Operation ${key} invocation`)
    if (invocation.kind !== connectionKind)
      throw new Error(`Every operation must use this connection's ${connectionKind} integration.`)
    if (invocation.kind === 'sql') {
      onlyKeys(invocation, ['kind', 'procedure'], `Operation ${key} SQL invocation`)
      const procedure = text(invocation.procedure, 'Stored procedure identifier')
      if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}\.[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(procedure))
        throw new Error('SQL catalogs accept schema.procedure identifiers only, never raw SQL.')
    } else if (invocation.kind === 'rest') {
      onlyKeys(invocation, ['kind', 'method', 'path'], `Operation ${key} REST invocation`)
      if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(String(invocation.method)))
        throw new Error(`Operation ${key} has an unsupported HTTP method.`)
      if (invocation.method === 'GET' && operation.effect !== 'read')
        throw new Error(`Operation ${key}: GET descriptions must be read-only.`)
      const path = text(invocation.path, `Operation ${key} path`, 500)
      if (
        !path.startsWith('/') ||
        path.startsWith('//') ||
        path.includes('//') ||
        /[\\?#%\s]/.test(path) ||
        path.split('/').some((segment) => segment === '.' || segment === '..')
      )
        throw new Error(
          'REST operations need root-relative paths without URLs, traversal, encoding, queries, or fragments.',
        )
      const stripped = path.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, name: string) => {
        if (!parameters.has(name))
          throw new Error(`Operation ${key}: path parameter "${name}" is not declared.`)
        return ''
      })
      if (/[{}]/.test(stripped))
        throw new Error(`Operation ${key} has an invalid path placeholder.`)
      const segments = path.slice(1).replace(/\/$/, '').split('/')
      if (
        path !== '/' &&
        segments.some(
          (segment) => !/^(?:[A-Za-z0-9_.~-]+|\{[A-Za-z_][A-Za-z0-9_]{0,63}\})$/.test(segment),
        )
      )
        throw new Error(
          `Operation ${key}: use whole path segments or whole parameter placeholders.`,
        )
      for (const rawField of list(operation.parameters, 'Parameters')) {
        const field = object(rawField, 'Parameter')
        const normalized = String(field.name)
          .toLowerCase()
          .replace(/[^a-z]/g, '')
        if (normalized === 'idempotencykey' || normalized === 'ifmatch')
          throw new Error(
            'REST transport headers are not caller parameter slots. Idempotency-Key is reserved for a future dispatcher; use declared expected_version for concurrency.',
          )
        if (field.required === false && path.includes(`{${String(field.name)}}`))
          throw new Error(`Operation ${key}: path parameters must be required.`)
        if (field.name === 'expected_version') {
          if (field.type !== 'string' || typeof field.max_length !== 'number')
            throw new Error(
              'Reserved expected_version must be a string with an explicit max_length.',
            )
          if (path.includes('{expected_version}'))
            throw new Error(
              'Reserved expected_version maps to future If-Match, never a path, query, or body field.',
            )
          if (
            Array.isArray(field.choices) &&
            field.choices.some(
              (choice) =>
                typeof choice !== 'string' ||
                !choice ||
                choice === '*' ||
                /[^\x21-\x7e]|"/.test(choice),
            )
          )
            throw new Error(
              'Expected-version choices must be bare opaque values without ETag quotes or whitespace.',
            )
        }
      }
    } else if (invocation.kind === 'graph') {
      onlyKeys(invocation, ['kind', 'template_key'], `Operation ${key} Graph invocation`)
      const template = text(invocation.template_key, `Operation ${key} template key`)
      if (!/^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(template))
        throw new Error('Graph catalogs must identify a fixed template, not message content.')
      if (operation.effect !== 'notify')
        throw new Error('Fixed-template Graph operations must have the notify effect.')
      const restricted = new Set([
        'sender',
        'from',
        'recipient',
        'recipients',
        'to',
        'cc',
        'bcc',
        'body',
        'subject',
        'attachments',
        'replyto',
        'message',
        'html',
        'content',
        'trustedlink',
      ])
      if ([...parameters].some((name) => restricted.has(name.toLowerCase().replace(/[^a-z]/g, ''))))
        throw new Error(
          'Graph parameters cannot choose senders, recipients, attachments, or message bodies.',
        )
    } else {
      throw new Error('Only SQL, REST, and fixed-template Graph catalogs are supported.')
    }
  }
}
