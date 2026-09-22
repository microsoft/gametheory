import { CatalogInspection } from './Catalog'
import type { ConfigurationContent } from './ConfigurationEditor'

export function ConfigurationDetails({ content }: { content: ConfigurationContent }) {
  return (
    <div>
      <dl className="preparation-meta">
        <dt>Classification</dt>
        <dd>
          {content.classification}
          {content.classification !== 'nonproduction' && ' · Execution ineligible'}
        </dd>
        <dt>Concrete resource</dt>
        <dd>
          <code>{content.resource_id || 'Unresolved'}</code>
        </dd>
        <dt>Endpoint metadata</dt>
        <dd>
          <code>{content.endpoint || 'Unresolved'}</code>
        </dd>
        <dt>Database metadata</dt>
        <dd>
          <code>{content.database || 'Not supplied'}</code>
        </dd>
        <dt>Identity reference</dt>
        <dd>
          <code>{content.identity_ref || 'Unresolved'}</code>
        </dd>
        <dt>Live readiness</dt>
        <dd>Unverified. Registration makes no target-system requests.</dd>
      </dl>
      {content.notification && (
        <section className="preparation-section">
          <h3>Fixed notification policy</h3>
          <dl className="preparation-meta">
            <dt>Template asset version</dt>
            <dd>
              <code>{content.notification.template_asset_id || 'Unresolved'}</code>
            </dd>
            <dt>Sender</dt>
            <dd>{content.notification.sender || 'Unresolved'}</dd>
            <dt>Explicit recipients</dt>
            <dd>
              {content.notification.recipients?.length ? (
                <ul>
                  {content.notification.recipients.map((recipient) => (
                    <li key={recipient}>{recipient}</li>
                  ))}
                </ul>
              ) : (
                'Unresolved'
              )}
            </dd>
            <dt>Trusted link</dt>
            <dd>
              <code>{content.notification.trusted_link || 'Unresolved'}</code>
            </dd>
            <dt>Delivery</dt>
            <dd>Unverified. No message is sent by this application.</dd>
          </dl>
        </section>
      )}
      <section className="preparation-section">
        <CatalogInspection catalog={content.catalog} />
      </section>
    </div>
  )
}
