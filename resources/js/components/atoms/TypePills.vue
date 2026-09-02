<script setup lang="ts">
import { computed, ref } from 'vue'
import { CONTENT_TYPE_OPTIONS } from '@/constants/contentTypes'

const props = withDefaults(defineProps<{
  modelValue: string
  includeAll?: boolean
  ariaLabel?: string
}>(), {
  includeAll: true,
  ariaLabel: 'Content type',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const visibleOptions = computed(() =>
  props.includeAll ? CONTENT_TYPE_OPTIONS : CONTENT_TYPE_OPTIONS.filter(o => o.value !== '')
)

const pills = ref<HTMLButtonElement[]>([])

/** Falls back to the first pill so Tab still reaches the group when the
 *  filter holds a value this group does not offer. */
const tabStop = computed(() => {
  const checked = visibleOptions.value.findIndex(o => o.value === props.modelValue)
  return checked === -1 ? 0 : checked
})

const next = (from: number, last: number) => (from === last ? 0 : from + 1)
const previous = (from: number, last: number) => (from === 0 ? last : from - 1)

const KEY_MOVES: Record<string, (from: number, last: number) => number> = {
  ArrowRight: next,
  ArrowDown: next,
  ArrowLeft: previous,
  ArrowUp: previous,
  Home: () => 0,
  End: (_from, last) => last,
}

const isBrowserShortcut = (e: KeyboardEvent) => e.altKey || e.ctrlKey || e.metaKey

function onKeydown(e: KeyboardEvent) {
  if (isBrowserShortcut(e)) return
  const move = KEY_MOVES[e.key]
  if (!move) return
  const from = pills.value.indexOf(e.target as HTMLButtonElement)
  if (from === -1) return

  e.preventDefault()
  const to = move(from, visibleOptions.value.length - 1)
  emit('update:modelValue', visibleOptions.value[to].value)
  pills.value[to].focus()
}
</script>

<template>
  <div class="badge-group" role="radiogroup" :aria-label="ariaLabel" @keydown="onKeydown">
    <button
      v-for="(opt, i) in visibleOptions"
      :key="opt.value"
      ref="pills"
      type="button"
      role="radio"
      class="badge"
      :aria-checked="modelValue === opt.value"
      :tabindex="i === tabStop ? 0 : -1"
      @click="emit('update:modelValue', opt.value)"
    >{{ opt.label }}</button>
  </div>
</template>
