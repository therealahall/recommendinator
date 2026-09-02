export const CONTENT_TYPE_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'book', label: 'Book' },
  { value: 'movie', label: 'Movie' },
  { value: 'tv_show', label: 'TV Show' },
  { value: 'video_game', label: 'Game' },
] as const

const CONTENT_TYPE_GLYPHS = {
  book: 'book',
  movie: 'movie',
  tv_show: 'tv',
  video_game: 'game',
} as const

export type ContentTypeGlyph =
  | (typeof CONTENT_TYPE_GLYPHS)[keyof typeof CONTENT_TYPE_GLYPHS]
  | 'image'

/** The generic mark, never a book: a named fallback glyph would call a game
 *  something it is not. */
export function contentTypeGlyph(contentType: string): ContentTypeGlyph {
  return CONTENT_TYPE_GLYPHS[contentType as keyof typeof CONTENT_TYPE_GLYPHS] ?? 'image'
}
