import { readFileSync } from 'node:fs'

/** Vite hands Vitest an empty stub for styles, and jsdom evaluates neither
 *  `:focus-visible` nor specificity, so a rule about either is assertable only
 *  as the source text of the component that carries it. */
export function componentStyles(relativePath: string): string {
  return readFileSync(`${process.cwd()}/${relativePath}`, 'utf8')
}
