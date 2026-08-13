import { PASSWORD_MISMATCH, passwordTooShort } from '@/constants/auth'

/** Shared so setup and change-password cannot drift on what they refuse.
 *  Length first: it is the rule the user has not been told yet. *minLength* is
 *  the server's own floor, so this refuses exactly what the API refuses. */
export function passwordComplaint(
  password: string,
  confirmation: string,
  minLength: number,
): string {
  if (password.length < minLength) return passwordTooShort(minLength)
  return password === confirmation ? '' : PASSWORD_MISMATCH
}
