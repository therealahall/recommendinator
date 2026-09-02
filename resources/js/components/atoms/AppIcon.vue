<script setup lang="ts">
import { computed } from 'vue'

// A sprite would need its <symbol> block inlined in the one document and
// referenced by a bare string; a typed name makes an unknown glyph a vue-tsc
// error instead of an icon that silently renders nothing.
const GLYPHS = {
  activity: { stroke: 1.9, d: ['M22 12h-4l-3 9L9 3l-3 9H2'] },
  check: { stroke: 2.2, d: ['m20 6-11 11-5-5'] },
  book: {
    stroke: 1.7,
    d: ['M4 19.5A2.5 2.5 0 0 1 6.5 17H20', 'M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'],
  },
  close: { stroke: 2, d: ['M18 6 6 18', 'm6 6 12 12'] },
  cog: {
    stroke: 1.7,
    d: [
      'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
      'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
    ],
  },
  copy: {
    stroke: 1.7,
    d: ['M11 9h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z', 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'],
  },
  menu: { stroke: 1.9, d: ['M3 6h18', 'M3 12h18', 'M3 18h18'] },
  minus: { stroke: 1.9, d: ['M5 12h14'] },
  plus: { stroke: 1.9, d: ['M12 5v14', 'M5 12h14'] },
  search: { stroke: 1.7, d: ['M11 18.5a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15z', 'm20.5 20.5-4-4'] },
  sliders: {
    stroke: 1.7,
    d: [
      'M4 6h16',
      'M9 8.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4z',
      'M4 12h16',
      'M15 14.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4z',
      'M4 18h16',
      'M9 20.2a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4z',
    ],
  },
  star: { stroke: 1.7, d: ['M12 2.6 15.1 8.9 22 9.9 17 14.8 18.2 21.7 12 18.4 5.8 21.7 7 14.8 2 9.9 8.9 8.9 12 2.6z'] },
} as const

const props = withDefaults(
  defineProps<{
    name: keyof typeof GLYPHS
    size?: 16 | 20 | 40
  }>(),
  { size: 16 },
)

const glyph = computed(() => GLYPHS[props.name])
const sizeClass = computed(() => (props.size === 16 ? '' : `icon--${props.size}`))
</script>

<template>
  <svg
    class="icon"
    :class="sizeClass"
    aria-hidden="true"
    focusable="false"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="glyph.stroke"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path v-for="d in glyph.d" :key="d" :d="d" />
  </svg>
</template>
