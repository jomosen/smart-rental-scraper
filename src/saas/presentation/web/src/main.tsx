import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import './styles/proto.css'
import App from './App.tsx'
import { UnauthorizedError } from './lib/auth'

const queryClient = new QueryClient({
  // Any 401 from a non-auth query (e.g. session expired mid-use) invalidates
  // the ['me'] query, which re-checks the session and drops back to login.
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (error instanceof UnauthorizedError && query.queryKey[0] !== 'me') {
        queryClient.invalidateQueries({ queryKey: ['me'] })
      }
    },
  }),
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: false } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
