import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { ToastHost } from './components/ToastHost'
import './styles/tokens.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Mutations invalidate explicitly; a short staleTime avoids refetching
      // on every navigation while keeping the lists reasonably fresh.
      staleTime: 15_000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
    mutations: {
      retry: 0,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <ToastHost />
    </QueryClientProvider>
  </StrictMode>,
)
