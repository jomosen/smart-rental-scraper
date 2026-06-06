import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Meta } from './types'
import { fetchMe, logout } from './lib/auth'
import Sidebar from './components/Sidebar'
import RadarView from './components/RadarView'
import LoginScreen from './components/LoginScreen'

export default function App() {
  const qc = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  // `plan` only lives in the cross-tariff meta; lift it for the sidebar.
  const [meta, setMeta] = useState<Meta | null>(null)

  const handleLogout = async () => {
    await logout()
    qc.removeQueries() // drop cached tenant data
    qc.invalidateQueries({ queryKey: ['me'] }) // → 401 → login
  }

  if (me.isLoading) {
    return <div className="login-screen" />
  }
  if (me.isError || !me.data) {
    return <LoginScreen />
  }

  return (
    <div className="app">
      <Sidebar tenantName={me.data.tenant_name} plan={meta?.plan ?? null} />
      <main className="main">
        <RadarView onMeta={setMeta} email={me.data.email} onLogout={handleLogout} />
      </main>
    </div>
  )
}
