<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import Accordion from '@/components/atoms/Accordion.vue'
import AppIcon from '@/components/atoms/AppIcon.vue'
import SettingMetaRow from '@/components/molecules/SettingMetaRow.vue'
import SettingsFieldList from '@/components/molecules/SettingsFieldList.vue'
import type { SettingBufferValue } from '@/composables/useSettingsBuffer'
import type { ProviderGroup } from '@/utils/settingsGroups'
import type { SettingViewValue } from '@/types/api'

const props = withDefaults(
  defineProps<{
    providers: ProviderGroup[]
    /** The buffered precedence order, which names providers that are off too. */
    order: string[]
    /** Absent where the section serves no order, leaving nothing to rank by. */
    orderSetting: SettingViewValue | null
    values: Record<string, SettingBufferValue>
    errors: Record<string, string>
    resetting: Record<string, boolean>
    secretBusy: Record<string, boolean>
    expanded: Record<string, boolean>
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  update: [key: string, value: SettingBufferValue]
  'update:order': [order: string[]]
  expand: [id: string, expanded: boolean]
  reset: [key: string]
  'set-secret': [key: string, value: string]
  'clear-secret': [key: string]
  'secret-draft': [key: string, hasDraft: boolean]
  announce: [message: string]
}>()

const RANKED_CAPTION = 'enrichment-provider-order-caption'
const OFF_CAPTION = 'enrichment-provider-off-caption'
const MOVE_LOCK = 'enrichment-provider-move-locked'

const root = ref<HTMLElement | null>(null)

function enabledKey(name: string): string {
  return `enrichment.providers.${name}.enabled`
}

function isOn(provider: ProviderGroup): boolean {
  return Boolean(props.values[enabledKey(provider.name)])
}

const ranks = computed(() => props.orderSetting !== null)

/** The stored order reconciled with what is installed: a provider it never names
 *  joins the end, and a name whose provider is gone leaves. Either shape is a
 *  save the service refuses. */
const effectiveOrder = computed(() => {
  const installed = new Set(props.providers.map((provider) => provider.name))
  const kept = props.order.filter((name) => installed.has(name))
  const named = new Set(kept)
  const missing = props.providers
    .map((provider) => provider.name)
    .filter((name) => !named.has(name))
    .sort()
  return [...kept, ...missing]
})

const ranked = computed(() => {
  if (!ranks.value) return []
  const rankOf = (provider: ProviderGroup): number =>
    effectiveOrder.value.indexOf(provider.name)
  return props.providers.filter(isOn).sort((one, two) => rankOf(one) - rankOf(two))
})

const unranked = computed(() =>
  props.providers
    .filter((provider) => !ranked.value.includes(provider))
    .sort((one, two) => one.label.localeCompare(two.label)),
)

/** Where a step lands in the order, or -1 where it cannot go: one predicate
 *  behind both the refusal and the guard, so no arrow reads live and then does
 *  nothing. */
function stepTarget(index: number, step: -1 | 1): number {
  const target = index + step
  if (target < 0 || target >= ranked.value.length) return -1
  return effectiveOrder.value.indexOf(ranked.value[target].name)
}

function refuses(index: number, step: -1 | 1): true | undefined {
  return props.disabled || stepTarget(index, step) === -1 ? true : undefined
}

async function move(index: number, step: -1 | 1): Promise<void> {
  const to = stepTarget(index, step)
  if (props.disabled || to === -1) return
  const moved = ranked.value[index]
  const next = [...effectiveOrder.value]
  const from = next.indexOf(moved.name)
  // A swap, not a splice: every name between the two is a provider that is off,
  // and a splice would drag it past a row the operator cannot see.
  ;[next[from], next[to]] = [next[to], next[from]]
  emit('update:order', next)
  emit(
    'announce',
    `${moved.label} moved to position ${index + step + 1} of ${ranked.value.length}.`,
  )
  await nextTick()
  // Keyed on the provider, not the position: the arrow that was pressed rode its
  // row down the list, and focus would otherwise sit on whichever row took its
  // place (WCAG 2.4.3).
  const direction = step < 0 ? 'up' : 'down'
  root.value
    ?.querySelector<HTMLElement>(
      `[data-provider="${moved.name}"] [data-direction="${direction}"]`,
    )
    ?.focus()
}

async function onUpdate(key: string, value: SettingBufferValue): Promise<void> {
  emit('update', key, value)
  const provider = props.providers.find((entry) => enabledKey(entry.name) === key)
  if (!provider) return
  await nextTick()
  // The row leaves one list for the other, unmounting the switch just pressed
  // and dropping focus to <body> (WCAG 2.4.3).
  document.getElementById(`setting-${key}`)?.focus()
  const position = ranked.value.indexOf(provider)
  emit(
    'announce',
    position === -1
      ? `${provider.label} off, and out of the precedence order.`
      : `${provider.label} on, at position ${position + 1} of ${ranked.value.length}.`,
  )
}
</script>

<template>
  <div ref="root" class="enrichment-providers">
    <p v-if="orderSetting?.help" class="source-form-help">{{ orderSetting.help }}</p>

    <template v-if="ranks">
      <p :id="RANKED_CAPTION" class="provider-list-caption">Precedence order</p>
      <!-- Explicit role="list": `list-style: none` drops list semantics in
           Safari/VoiceOver, and position-of-length is the only way to read the
           order back on demand once an announcement has passed. -->
      <ol
        v-if="ranked.length > 0"
        class="provider-list"
        role="list"
        data-testid="provider-order"
        :aria-labelledby="RANKED_CAPTION"
      >
        <li v-for="(provider, index) in ranked" :key="provider.name" :data-provider="provider.name">
          <Accordion
            :id="provider.id"
            :heading-level="4"
            :expanded="expanded[provider.id] ?? false"
            class="provider-accordion"
            @update:expanded="emit('expand', provider.id, $event)"
          >
            <template #header>
              <span class="provider-header">
                <span class="provider-name">{{ provider.label }}</span>
                <span class="badge" data-tone="accent">
                  <span aria-hidden="true">{{ index + 1 }}</span>
                  <span class="sr-only">Position {{ index + 1 }} of {{ ranked.length }}</span>
                </span>
              </span>
            </template>

            <template #header-actions>
              <!-- aria-disabled at the ends, not disabled: the arrow pressed to
                   reach position 1 would blur and leave the operator tabbing
                   from the top of the page (WCAG 2.4.3). -->
              <button
                type="button"
                class="provider-move"
                data-direction="up"
                :aria-label="`Move ${provider.label} up`"
                :aria-disabled="refuses(index, -1)"
                :aria-describedby="disabled ? MOVE_LOCK : undefined"
                @click="move(index, -1)"
              ><AppIcon name="arrow-up" /></button>
              <button
                type="button"
                class="provider-move"
                data-direction="down"
                :aria-label="`Move ${provider.label} down`"
                :aria-disabled="refuses(index, 1)"
                :aria-describedby="disabled ? MOVE_LOCK : undefined"
                @click="move(index, 1)"
              ><AppIcon name="arrow-down" /></button>
            </template>

            <SettingsFieldList
              :settings="provider.settings"
              :values="values"
              :disabled="disabled"
              :errors="errors"
              :resetting="resetting"
              :secret-busy="secretBusy"
              @update="onUpdate"
              @reset="emit('reset', $event)"
              @set-secret="(key, value) => emit('set-secret', key, value)"
              @clear-secret="emit('clear-secret', $event)"
              @secret-draft="(key, draft) => emit('secret-draft', key, draft)"
            />
          </Accordion>
        </li>
      </ol>
      <p v-else class="state">No provider is on, so nothing is ranked yet.</p>
    </template>

    <p v-if="ranks && unranked.length > 0" :id="OFF_CAPTION" class="provider-list-caption">
      Not in the order
    </p>
    <ul
      v-if="unranked.length > 0"
      class="provider-list"
      role="list"
      data-testid="provider-unranked"
      :aria-labelledby="ranks ? OFF_CAPTION : undefined"
      :aria-label="ranks ? undefined : 'Enrichment providers'"
    >
      <li v-for="provider in unranked" :key="provider.name" :data-provider="provider.name">
        <Accordion
          :id="provider.id"
          :heading-level="4"
          :expanded="expanded[provider.id] ?? false"
          class="provider-accordion"
          @update:expanded="emit('expand', provider.id, $event)"
        >
          <template #header>
            <span class="provider-header">
              <span class="provider-name">{{ provider.label }}</span>
              <span v-if="!isOn(provider)" class="badge">Off</span>
            </span>
          </template>

          <SettingsFieldList
            :settings="provider.settings"
            :values="values"
            :disabled="disabled"
            :errors="errors"
            :resetting="resetting"
            :secret-busy="secretBusy"
            @update="onUpdate"
            @reset="emit('reset', $event)"
            @set-secret="(key, value) => emit('set-secret', key, value)"
            @clear-secret="emit('clear-secret', $event)"
            @secret-draft="(key, draft) => emit('secret-draft', key, draft)"
          />
        </Accordion>
      </li>
    </ul>

    <span v-if="disabled" :id="MOVE_LOCK" class="sr-only"
      >Unavailable while this section has a change in flight.</span
    >

    <SettingMetaRow
      v-if="orderSetting"
      :setting="orderSetting"
      :disabled="disabled"
      :resetting="resetting[orderSetting.key] ?? false"
      @reset="emit('reset', orderSetting.key)"
    />
  </div>
</template>

<style scoped>
.enrichment-providers {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-3) 0;
}

/* Names each list without a fourth heading level under the section: the provider
   rows are already the h4s a screen reader navigates by. */
.provider-list-caption {
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-wide);
  margin-top: var(--space-2);
  text-transform: uppercase;
}

.provider-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  list-style: none;
}

/* Flat, like the subgroup it replaces: a nested card at the section's own
   elevation reads as a sibling of the section rather than as its contents. */
.provider-accordion {
  box-shadow: var(--elevation-0);
}

.provider-header {
  align-items: baseline;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.provider-name {
  font-weight: var(--weight-semibold);
}

.provider-move {
  align-items: center;
  background: transparent;
  border: none;
  color: var(--text-primary);
  cursor: pointer;
  display: inline-flex;
  justify-content: center;
  min-height: 32px;
  min-width: 32px;
  padding: 0;
}

/* base.css's lock for a button that keeps focus (WCAG 2.4.3): its own fill, not
   a mark on the glyph, which a rule through an arrow read as. --text-muted
   returns with it: --bg-hover no longer reaches the glyph. */
.provider-move[aria-disabled='true'] {
  background: var(--bg-primary);
  border: 1px solid var(--border-default);
  color: var(--text-muted);
  cursor: not-allowed;
}
</style>
