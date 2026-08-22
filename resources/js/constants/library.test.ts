import { readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'
import { DEFAULT_SORT, MAX_CREATOR_LENGTH, RELEASE_YEAR_TYPES, SORT_OPTIONS } from './library'

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

function pythonSortOptions(source: string): string[] {
  const table = source.match(/^_SORT_ORDER_BY: dict\[str, str\] = \{\n([\s\S]*?)\n\}$/m)![1]
  return [...table.matchAll(/^ {4}"(\w+)":/gm)].map(match => match[1])
}

describe('the sort orders the library select mirrors from Python', () => {
  it('offers exactly the orders the API accepts, and defaults to one of them', () => {
    // Drop an order from the whitelist and the select still sends it, which
    // comes back 400 and empties the library view.
    const db = readFileSync(`${process.cwd()}/src/storage/sqlite_db.py`, 'utf8')

    const offered = SORT_OPTIONS.map(option => option.value)
    expect([...offered].sort()).toEqual(pythonSortOptions(db).sort())
    expect(offered).toContain(DEFAULT_SORT)
  })
})

describe('the correction facts the edit dialog mirrors from Python', () => {
  it('bounds a correction, and offers a year box, exactly where the API does', () => {
    // Widen MAX_CREATOR_LENGTH in Python alone and the box stops accepting a
    // name `library edit --creator` stores.
    const content = readFileSync(`${process.cwd()}/src/models/content.py`, 'utf8')
    const fields = readFileSync(`${process.cwd()}/src/models/detail_fields.py`, 'utf8')

    expect(MAX_CREATOR_LENGTH).toBe(pythonInt(content, 'MAX_CREATOR_LENGTH'))
    expect([...RELEASE_YEAR_TYPES].sort()).toEqual(typesDeclaringAReleaseYear(fields).sort())
  })
})
