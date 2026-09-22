import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { InteractionRequiredAuthError, PublicClientApplication } from '@azure/msal-browser'
import { MsalProvider, useMsal } from '@azure/msal-react'
import { useQuery } from '@tanstack/react-query'
import { Link, Route, Routes } from 'react-router-dom'
import { Moon, Sun, LogOut } from 'lucide-react'
import { createApi, ErrorNotice, SessionContext, useSession } from './api'
import type { Config } from './types'
import { Library, WorkspacePage } from './Library'
import { Studio } from './Studio'
import type { Me } from './preparation'

const AdminSettings = lazy(() =>
  import('./AdminSettings').then((module) => ({ default: module.AdminSettings })),
)
const ExerciseRun = lazy(() =>
  import('./ExerciseRuns').then((module) => ({ default: module.ExerciseRun })),
)

function AdminSettingsLink() {
  const { api } = useSession()
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/me') })
  return me.data?.organization_admin && !me.error ? <Link to="/settings">Settings</Link> : null
}

const PreparationBoard = lazy(() =>
  import('./PreparationBoard').then((module) => ({ default: module.PreparationBoard })),
)
const ConnectionConfigurations = lazy(() =>
  import('./ConnectionConfigurations').then((module) => ({
    default: module.ConnectionConfigurations,
  })),
)

export function ThemeButton() {
  const [dark, setDark] = useState(() => localStorage.getItem('gt-theme') === 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    localStorage.setItem('gt-theme', dark ? 'dark' : 'light')
  }, [dark])
  return (
    <button
      className="icon-button"
      onClick={() => setDark(!dark)}
      aria-label={dark ? 'Use light theme' : 'Use dark theme'}
    >
      {dark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  )
}

export function Brand() {
  return (
    <span className="brand">
      <span className="brand-mark" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      game<span>theory</span>
    </span>
  )
}

export function App() {
  const config = useQuery({
    queryKey: ['config'],
    queryFn: async (): Promise<Config> => {
      const response = await fetch('/api/config')
      if (!response.ok)
        throw new Error('Cannot load application configuration. Check the API service.')
      return response.json()
    },
  })
  const [instance, setInstance] = useState<PublicClientApplication>()
  const [error, setError] = useState<unknown>()
  useEffect(() => {
    if (!config.data?.auth.configured) return
    let active = true
    const auth = new PublicClientApplication({
      auth: {
        clientId: config.data.auth.client_id,
        authority: config.data.auth.authority,
        redirectUri: window.location.origin,
        navigateToLoginRequestUrl: true,
      },
      cache: { cacheLocation: 'sessionStorage' },
    })
    void auth
      .initialize()
      .then(() => auth.handleRedirectPromise())
      .then((result) => {
        if (!active) return
        auth.setActiveAccount(result?.account ?? auth.getAllAccounts()[0] ?? null)
        setInstance(auth)
      })
      .catch((cause) => {
        if (active) setError(cause)
      })
    return () => {
      active = false
    }
  }, [config.data])
  if (!config.data || !config.data.auth.configured || !instance)
    return (
      <div className="welcome">
        <header>
          <Brand />
          <ThemeButton />
        </header>
        <main className="glass welcome-card">
          <span className="eyebrow">SCENARIO ORCHESTRATION</span>
          <h1>Prepare for what comes next.</h1>
          <p>Build realistic scenarios. Coordinate your organization. Learn from evidence.</p>
          <ErrorNotice error={error ?? config.error} />
          {config.data && !config.data.auth.configured ? (
            <div className="notice">
              <strong>Deployment configuration required</strong>
              <p>
                Configure the Entra tenant, SPA registration, API audience, and delegated scope.
                This app does not bypass sign-in or substitute sample data.
              </p>
              <code>See docs/development.md</code>
            </div>
          ) : (
            !error && !config.error && <p role="status">Connecting to your organization...</p>
          )}
        </main>
      </div>
    )
  return (
    <MsalProvider instance={instance}>
      <Authenticated config={config.data} />
    </MsalProvider>
  )
}

function Authenticated({ config }: { config: Config }) {
  const { instance, accounts } = useMsal()
  const [error, setError] = useState<unknown>()
  const api = useMemo(
    () =>
      createApi(async () => {
        const account = instance.getActiveAccount()
        if (!account) throw new Error('Sign in to continue')
        try {
          return (await instance.acquireTokenSilent({ scopes: [config.auth.scope], account }))
            .accessToken
        } catch (cause) {
          if (cause instanceof InteractionRequiredAuthError) {
            await instance.acquireTokenRedirect({ scopes: [config.auth.scope], account })
            throw new Error('Sign-in renewal is required')
          }
          throw cause
        }
      }),
    [instance, config.auth.scope],
  )
  if (!accounts.length)
    return (
      <div className="welcome">
        <header>
          <Brand />
          <ThemeButton />
        </header>
        <main className="glass welcome-card">
          <span className="eyebrow">YOUR ORGANIZATION'S STUDIO</span>
          <h1>Plan with purpose.</h1>
          <p>Sign in with your organization account to access your workspaces.</p>
          <ErrorNotice error={error} />
          <button
            className="primary"
            onClick={() =>
              void instance.loginRedirect({ scopes: [config.auth.scope] }).catch(setError)
            }
          >
            Sign in with Microsoft
          </button>
        </main>
      </div>
    )
  return (
    <SessionContext.Provider value={{ api, config }}>
      <div className="app-shell">
        <header className="app-header">
          <Link to="/" aria-label="Game Theory workspaces">
            <Brand />
          </Link>
          <span className="header-label">
            Scenario studio <span className="pill">{config.cloud}</span>
          </span>
          <div className="header-actions">
            <AdminSettingsLink />
            <span className="account-name">{accounts[0].name}</span>
            <ThemeButton />
            <button
              className="icon-button"
              aria-label="Sign out"
              onClick={() => void instance.logoutRedirect().catch(setError)}
            >
              <LogOut size={18} />
            </button>
          </div>
        </header>
        <ErrorNotice error={error} />
        {!config.capabilities.authoring && (
          <div className="notice error">
            Application SQL storage is not configured. Authoring is unavailable.
          </div>
        )}
        <Routes>
          <Route path="/" element={<Library />} />
          <Route
            path="/settings"
            element={
              <Suspense
                fallback={
                  <main className="page" role="status">
                    Opening settings...
                  </main>
                }
              >
                <AdminSettings />
              </Suspense>
            }
          />
          <Route
            path="/w/:wid/runs/:rid"
            element={
              <Suspense
                fallback={
                  <main className="page" role="status">
                    Opening exercise run...
                  </main>
                }
              >
                <ExerciseRun />
              </Suspense>
            }
          />
          <Route path="/w/:wid" element={<WorkspacePage />} />
          <Route path="/w/:wid/s/:sid" element={<Studio />} />
          <Route
            path="/w/:wid/boards/:bid"
            element={
              <Suspense
                fallback={
                  <main className="page" role="status">
                    Opening preparation board...
                  </main>
                }
              >
                <PreparationBoard />
              </Suspense>
            }
          />
          <Route
            path="/w/:wid/connections/:cid"
            element={
              <Suspense
                fallback={
                  <main className="page" role="status">
                    Opening connection configuration...
                  </main>
                }
              >
                <ConnectionConfigurations />
              </Suspense>
            }
          />
          <Route
            path="*"
            element={
              <main className="page">
                <h1>Page not found</h1>
                <Link to="/">Return to workspaces</Link>
              </main>
            }
          />
        </Routes>
      </div>
    </SessionContext.Provider>
  )
}
