/** Shared by the setup screen and the Settings change-password form, which are
 *  the two places a confirmation field can disagree with the one above it. */
export const PASSWORD_MISMATCH = 'Those two passwords do not match. Retype them and try again.'

// Mirrors MIN_PASSWORD_LENGTH in src/storage/accounts.py, and is only the
// default: GET /api/auth/session carries the server's own figure, which is what
// every screen states once that call has answered.
export const PASSWORD_MIN_LENGTH = 12

/** Mirrors MAX_ACCOUNT_NAME_LENGTH in src/storage/accounts.py: past it the API
 *  refuses the save with a validation shape that renders as no sentence. */
export const NAME_MAX_LENGTH = 100

export function passwordHint(minLength: number): string {
  return `At least ${minLength} characters.`
}

export function passwordTooShort(minLength: number): string {
  return `That password is shorter than ${minLength} characters. Lengthen it and try again.`
}
