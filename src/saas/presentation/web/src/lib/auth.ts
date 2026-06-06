// Auth API + the shared 401 signal used to bounce back to the login screen.

export class UnauthorizedError extends Error {
  constructor() {
    super('Unauthorized')
    this.name = 'UnauthorizedError'
  }
}

export interface Me {
  email: string
  tenant_name: string
}

export async function fetchMe(): Promise<Me> {
  const res = await fetch('/api/auth/me')
  if (res.status === 401) throw new UnauthorizedError()
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<Me>
}

export async function requestLink(email: string): Promise<void> {
  await fetch('/api/auth/request-link', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  // Always resolves — the endpoint is intentionally constant-response.
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' })
}
