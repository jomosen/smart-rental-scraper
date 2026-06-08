import { useState } from 'react'
import { login, InvalidCredentialsError } from '../lib/auth'

/** Email + password login. Calls onLoggedIn() so the app can refetch /me. */
export default function LoginScreen({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!email.trim() || !password || busy) return
    setBusy(true)
    setError('')
    try {
      await login(email.trim(), password)
      onLoggedIn()
    } catch (e) {
      setError(
        e instanceof InvalidCredentialsError
          ? 'Correo o contraseña incorrectos.'
          : (e as Error).message || 'No se pudo iniciar sesión.',
      )
      setBusy(false)
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <b>Acceso</b>
        </div>
        <p className="lsub">Accede a tu panel de precios con tu correo y contraseña.</p>

        <div>
          <label htmlFor="lemail">Correo electrónico</label>
          <input
            type="email"
            id="lemail"
            placeholder="tu@empresa.com"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
          />
          <label htmlFor="lpass">Contraseña</label>
          <input
            type="password"
            id="lpass"
            placeholder="••••••••"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
          />
          {error && <p className="login-error" role="alert">{error}</p>}
          <button className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? 'Entrando…' : 'Entrar'}
          </button>
        </div>

        <div className="login-foot">Acceso restringido a clientes</div>
      </div>
    </div>
  )
}
