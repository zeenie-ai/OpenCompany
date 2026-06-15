import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
// Theme CSS — load BEFORE index.css so the per-theme [data-theme="..."]
// blocks register their tokens before Tailwind compiles `@theme inline`
// against the cascade. Order within the theme list matches specificity:
// base.css (neutral defaults) → light.css (default :root + [data-theme="light"])
// → dark.css (.dark + [data-theme="dark"]) → renaissance + cyber overrides.
import './themes/base.css'
import './themes/light.css'
import './themes/dark.css'
// Utopian set
import './themes/renaissance.css'
import './themes/greek.css'
import './themes/edo.css'
import './themes/steampunk.css'
import './themes/atomic.css'
// Dystopian set
import './themes/cyber.css'
import './themes/wasteland.css'
import './themes/rot.css'
import './themes/plague.css'
import './themes/surveillance.css'
// Animation system — pulse-keyframe tokens, trigger armed/listening motion,
// .machina-* helpers. Loaded after the themes so its keyframes + per-theme
// --pulse-* tokens are authoritative.
import './themes/animations.css'
import './index.css'
import App from './App'
import { ThemeProvider } from './contexts/ThemeContext'
import { AuthProvider } from './contexts/AuthContext'
import { WebSocketProvider } from './contexts/WebSocketContext'
import { queryClient } from './lib/queryClient'
import {
  queryPersister,
  queryBuster,
  queryPersistMaxAge,
  shouldPersistQuery,
} from './lib/queryPersist'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister: queryPersister,
        buster: queryBuster,
        maxAge: queryPersistMaxAge,
        dehydrateOptions: { shouldDehydrateQuery: shouldPersistQuery },
      }}
    >
      <ThemeProvider>
        <AuthProvider>
          <WebSocketProvider>
            <App />
          </WebSocketProvider>
        </AuthProvider>
      </ThemeProvider>
      {import.meta.env.DEV && (
        <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-right" />
      )}
    </PersistQueryClientProvider>
  </StrictMode>,
)
