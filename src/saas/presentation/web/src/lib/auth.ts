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

/** Throws InvalidCredentialsError on 401, generic Error otherwise. */
export class InvalidCredentialsError extends Error {
  constructor() {
    super('Invalid credentials')
    this.name = 'InvalidCredentialsError'
  }
}

export async function login(email: string, password: string): Promise<void> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (res.status === 401) throw new InvalidCredentialsError()
  if (res.status === 429) throw new Error('Demasiados intentos. Inténtalo más tarde.')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' })
}
