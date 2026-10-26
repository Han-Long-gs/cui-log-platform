import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/fragment-mono'
import '@fontsource/monofett'
import "./styles/variables.css"
import "./styles/base.css"
import "./styles/layout.css"
import "./styles/components.css"
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
