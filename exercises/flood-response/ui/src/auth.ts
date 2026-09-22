import {
  BrowserCacheLocation,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from '@azure/msal-browser';
import type { AuthConfig } from './config';

export type Identity = { displayName: string; accountId: string };

export interface Authentication {
  initialize(): Promise<Identity | null>;
  signIn(): Promise<Identity>;
  signOut(): Promise<void>;
  token(): Promise<string>;
}

export class EntraAuthentication implements Authentication {
  private readonly client: PublicClientApplication;
  private readonly scopes: string[];

  constructor(config: AuthConfig) {
    this.scopes = [config.apiScope];
    this.client = new PublicClientApplication({
      auth: {
        clientId: config.clientId,
        authority: `https://login.microsoftonline.com/${config.tenantId}`,
        redirectUri: config.redirectUri,
        postLogoutRedirectUri: config.redirectUri,
        navigateToLoginRequestUrl: false,
      },
      cache: { cacheLocation: BrowserCacheLocation.SessionStorage },
      system: { loggerOptions: { piiLoggingEnabled: false, loggerCallback: () => undefined } },
    });
  }

  async initialize(): Promise<Identity | null> {
    await this.client.initialize();
    const response = await this.client.handleRedirectPromise();
    const account = response?.account ?? this.client.getActiveAccount() ?? this.client.getAllAccounts()[0];
    if (!account) return null;
    this.client.setActiveAccount(account);
    return { displayName: account.name ?? 'Signed-in participant', accountId: account.homeAccountId };
  }

  async signIn(): Promise<Identity> {
    const response = await this.client.loginPopup({ scopes: this.scopes, prompt: 'select_account' });
    if (!response.account) throw new Error('Sign-in did not return an account.');
    this.client.setActiveAccount(response.account);
    return { displayName: response.account.name ?? 'Signed-in participant', accountId: response.account.homeAccountId };
  }

  async signOut(): Promise<void> {
    await this.client.logoutPopup({ account: this.client.getActiveAccount() });
  }

  async token(): Promise<string> {
    const account = this.client.getActiveAccount();
    if (!account) throw new Error('Sign in again to continue.');
    try {
      return (await this.client.acquireTokenSilent({ account, scopes: this.scopes })).accessToken;
    } catch (error) {
      if (error instanceof InteractionRequiredAuthError) {
        // Popup acquisition only follows an explicit user action, never an automatic read.
        throw new Error('Your session needs attention. Sign out and sign in again.');
      }
      throw new Error('Your access token is unavailable. Sign in again.');
    }
  }
}
