// One wording for every web door to a reset, the CLI prompt's own, so the bulk
// toolbar and the edit dialog cannot drift apart on what a reset destroys.
export function resetQuestion(asked: string, legacySubject: string, hard: boolean): string {
  const question =
    `${asked} Everything those providers stated goes — genres, tags, descriptions, ` +
    'runtimes and the rest — and each item is rebuilt on what its sources, your ' +
    'edits and its pins say. The next run refills it from rate-limited APIs.'
  if (!hard) return question
  return (
    question +
    ` It also deletes the values no writer ever claimed, ${legacySubject}, ` +
    'cover art included: the next enrichment run refetches a cover, and anything ' +
    'else comes back only when the writer that supplied it states it again. Your ' +
    'edits, pins, ratings and reviews stay, but a creator you typed at the ' +
    'completion door before this release goes with the rest.'
  )
}
