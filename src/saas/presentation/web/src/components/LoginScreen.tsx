import { useState } from 'react'
import { requestLink } from '../lib/auth'

/** Magic-link login, ported from the proto (no demo button). */
export default function LoginScreen() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)

  const send = async () => {
    if (!email.trim() || busy) return
    setBusy(true)
    try {
      await requestLink(email.trim())
    } finally {
      setBusy(false)
      setSent(true) // constant response — always show the neutral "check your inbox"
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <span className="brand-dot" />
          <b>RentRadar</b>
        </div>
        <p className="lsub">
          Accede a tu panel de precios. Te enviaremos un enlace de acceso a tu correo — sin contraseñas.
        </p>

        {!sent ? (
          <div>
            <label htmlFor="lemail">Correo electrónico</label>
            <input
              type="email"
              id="lemail"
              placeholder="tu@empresa.com"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') send() }}
            />
            <button className="btn-primary" onClick={send} disabled={busy}>
              {busy ? 'Enviando…' : 'Enviarme el enlace de acceso'}
            </button>
          </div>
        ) : (
          <div className="login-sent" style={{ display: 'block' }}>
            <div className="mail-ic">✉️</div>
            <p>
              Si tu correo está registrado, recibirás un enlace de acceso en unos segundos.
              <br />Revisa tu bandeja de entrada.
            </p>
          </div>
        )}

        <div className="login-foot">Acceso restringido a clientes · RentRadar</div>
      </div>
    </div>
  )
}
