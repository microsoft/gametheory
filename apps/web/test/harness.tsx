import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, Outlet, RouterProvider } from 'react-router-dom'
import { SessionContext, createApi } from '../src/api'
import { Brand, ThemeButton } from '../src/App'
import { Studio, StudioContent } from '../src/Studio'
import { Library, WorkspacePage } from '../src/Library'
import { PreparationBoard } from '../src/PreparationBoard'
import { ConnectionConfigurations } from '../src/ConnectionConfigurations'
import '../src/styles.css'

if (!import.meta.env.DEV || import.meta.env.MODE !== 'test') {
  throw new Error('This explicit fixture harness is available only in Vite test mode')
}
const cache = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
})
const router = createBrowserRouter([
  {
    path: '/',
    element: (
      <>
        <header className="app-header">
          <Brand />
          <span className="pill">TEST FIXTURE — NOT LIVE DATA</span>
          <ThemeButton />
        </header>
        <Outlet />
      </>
    ),
    children: [
      { index: true, element: <Library /> },
      {
        path: 'test.html',
        element: (
          <StudioContent
            wid="11111111-1111-4111-8111-111111111111"
            sid="22222222-2222-4222-8222-222222222222"
          />
        ),
      },
      { path: 'w/:wid', element: <WorkspacePage /> },
      { path: 'w/:wid/s/:sid', element: <Studio /> },
      { path: 'w/:wid/boards/:bid', element: <PreparationBoard /> },
      { path: 'w/:wid/connections/:cid', element: <ConnectionConfigurations /> },
    ],
  },
  { path: '*', element: <p>Left the test studio.</p> },
])
ReactDOM.createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={cache}>
    <SessionContext.Provider
      value={{
        api: createApi(async () => 'explicit-e2e-fixture-token'),
        config: {
          cloud: 'commercial',
          auth: { configured: true, client_id: 'test', authority: '', scope: '' },
          capabilities: { authoring: true, assets: true, planning: true, execution: false },
          max_upload_bytes: 10485760,
        },
      }}
    >
      <RouterProvider router={router} />
    </SessionContext.Provider>
  </QueryClientProvider>,
)
