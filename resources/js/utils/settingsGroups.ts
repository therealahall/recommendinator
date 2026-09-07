import { humanizeSection } from '@/utils/format'
import type { SettingView } from '@/types/api'

export interface SettingGroup {
  id: string
  label: string
  settings: SettingView[]
}

export interface GroupedSettings {
  ungrouped: SettingView[]
  groups: SettingGroup[]
}

/** A key deeper than `section.leaf` names a subgroup: both
 *  `enrichment.providers.tmdb.api_key` and `recommendations.scorer_weights.*`
 *  group on everything left of the leaf. */
function groupPath(key: string): string {
  const parts = key.split('.')
  return parts.length < 3 ? '' : parts.slice(0, -1).join('.')
}

function slug(path: string): string {
  return path.replace(/[^a-zA-Z0-9_-]/g, '-')
}

const NON_ALNUM = /[^a-z0-9]/g

function normalize(value: string): string {
  return value.toLowerCase().replace(NON_ALNUM, '')
}

/** The heading is read back off the registry's own labels ("TMDB API key",
 *  "Open Library enabled"): a list of provider names here would need editing
 *  every time a provider is added. */
function labelFor(path: string, settings: SettingView[]): string {
  const leaf = path.slice(path.lastIndexOf('.') + 1)
  const target = normalize(leaf)
  for (const setting of settings) {
    const words = setting.label.split(/\s+/).filter(Boolean)
    for (let start = 0; start < words.length; start += 1) {
      let run = ''
      for (let end = start; end < words.length; end += 1) {
        run = normalize(run + words[end])
        if (run === target) return words.slice(start, end + 1).join(' ')
        if (run.length >= target.length) break
      }
    }
  }
  return humanizeSection(leaf)
}

export function groupSettings(settings: SettingView[]): GroupedSettings {
  const byPath = new Map<string, SettingView[]>()
  for (const setting of settings) {
    const path = groupPath(setting.key)
    if (path === '') continue
    const members = byPath.get(path)
    if (members) members.push(setting)
    else byPath.set(path, [setting])
  }
  // A disclosure around a single control costs two tab stops and a click to
  // reach it, and hides it from find-in-page. It only pays for a pair.
  const kept = Array.from(byPath).filter(([, members]) => members.length > 1)
  const keptPaths = new Set(kept.map(([path]) => path))
  return {
    ungrouped: settings.filter((setting) => !keptPaths.has(groupPath(setting.key))),
    groups: kept.map(([path, members]) => ({
      id: slug(path),
      label: labelFor(path, members),
      settings: members,
    })),
  }
}
