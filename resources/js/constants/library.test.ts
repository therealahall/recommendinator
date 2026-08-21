import { readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'
import {
  MAX_CREATOR_LENGTH,
  MAX_RELEASE_YEAR,
  MIN_RELEASE_YEAR,
  RELEASE_YEAR_TYPES,
} from './library'

function pythonInt(source: string, name: string): number {
  return Number(source.match(new RegExp(`^${name} = (\\d+)$`, 'm'))![1])
}

function typesDeclaringAReleaseYear(source: string): string[] {
  const table = source.match(/^DETAIL_FIELDS[^\n]*\{\n([\s\S]*?)\n\}$/m)![1]
  const declarations = table.split(/^ {4}"(\w+)": ContentTypeFields\(/m)
  const declared: string[] = []
  for (let i = 1; i < declarations.length; i += 2) {
    if (/DetailField\(\s*"release_year"/.test(declarations[i + 1])) {
      declared.push(declarations[i])
    }
  }
  return declared
}

describe('the correction facts the edit dialog mirrors from Python', () => {
  it('bounds a correction, and offers a year box, exactly where the API does', () => {
    // Widen MAX_RELEASE_YEAR in Python alone and the dialog keeps refusing a
    // year `library edit --release-year` stores.
    const content = readFileSync(`${process.cwd()}/src/models/content.py`, 'utf8')
    const fields = readFileSync(`${process.cwd()}/src/models/detail_fields.py`, 'utf8')

    expect(MIN_RELEASE_YEAR).toBe(pythonInt(content, 'MIN_RELEASE_YEAR'))
    expect(MAX_RELEASE_YEAR).toBe(pythonInt(content, 'MAX_RELEASE_YEAR'))
    expect(MAX_CREATOR_LENGTH).toBe(pythonInt(content, 'MAX_CREATOR_LENGTH'))
    expect([...RELEASE_YEAR_TYPES].sort()).toEqual(typesDeclaringAReleaseYear(fields).sort())
  })
})
