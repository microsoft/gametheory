export type AuthConfig = {
  tenantId: string;
  clientId: string;
  apiScope: string;
  apiBaseUrl: string;
  redirectUri: string;
};

export type Configuration = { ready: true; value: AuthConfig } | { ready: false };

export function configuration(env: ImportMetaEnv = import.meta.env): Configuration {
  const tenantId = env.VITE_ENTRA_TENANT_ID?.trim() ?? '';
  const clientId = env.VITE_ENTRA_CLIENT_ID?.trim() ?? '';
  const apiScope = env.VITE_ENTRA_API_SCOPE?.trim() ?? '';
  const apiBaseUrl = env.VITE_API_BASE_URL?.trim().replace(/\/$/, '') ?? '';
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  try {
    const base = new URL(apiBaseUrl);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname);
    if (
      !uuid.test(tenantId) ||
      !uuid.test(clientId) ||
      !apiScope.startsWith('api://') ||
      apiScope.endsWith('/.default') ||
      apiScope.split('/').length < 4 ||
      base.username ||
      base.password ||
      base.search ||
      base.hash ||
      (base.protocol !== 'https:' && !(local && base.protocol === 'http:'))
    ) return { ready: false };
    return {
      ready: true,
      value: {
        tenantId,
        clientId,
        apiScope,
        apiBaseUrl,
        redirectUri: `${window.location.origin}/`,
      },
    };
  } catch {
    return { ready: false };
  }
}
