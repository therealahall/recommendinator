import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true, gfm: true })

const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li',
    'blockquote', 'h1', 'h2', 'h3', 'h4', 'a',
  ],
  ALLOWED_ATTR: ['href', 'title'],
  ALLOW_DATA_ATTR: false,
  RETURN_TRUSTED_TYPE: false as const,
}

// Only allow https:// URLs and #fragment anchors in links.
// Blocks javascript:, data:, protocol-relative (//evil.com), and http:// URIs.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.hasAttribute('href')) {
    const href = node.getAttribute('href') ?? ''
    if (!/^https:\/\//i.test(href) && !href.startsWith('#')) {
      node.removeAttribute('href')
    }
  }
})

export function useMarkdown() {
  function renderMarkdown(text: string): string {
    return DOMPurify.sanitize(marked.parse(text) as string, PURIFY_CONFIG) as string
  }

  return { renderMarkdown }
}
