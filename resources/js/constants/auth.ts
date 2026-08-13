/** Shared by the setup screen and the Settings change-password form, which are
 *  the two places a confirmation field can disagree with the one above it. */
export const PASSWORD_MISMATCH = 'Those two passwords do not match. Retype them and try again.'

// Mirrors PASSWORD_MIN_LENGTH in src/auth: the API refuses a shorter one with a
// validation shape that renders as no sentence at all, so the rule has to be on
// screen before the user submits.
export const PASSWORD_MIN_LENGTH = 12

export const PASSWORD_HINT = `At least ${PASSWORD_MIN_LENGTH} characters.`

export const PASSWORD_TOO_SHORT = `That password is shorter than ${PASSWORD_MIN_LENGTH} characters. Lengthen it and try again.`
