import { PASSWORD_MISMATCH, PASSWORD_MIN_LENGTH, PASSWORD_TOO_SHORT } from '@/constants/auth'

/** Shared so setup and change-password cannot drift on what they refuse.
 *  Length first: it is the rule the user has not been told yet. */
export function passwordComplaint(password: string, confirmation: string): string {
  if (password.length < PASSWORD_MIN_LENGTH) return PASSWORD_TOO_SHORT
  return password === confirmation ? '' : PASSWORD_MISMATCH
}
