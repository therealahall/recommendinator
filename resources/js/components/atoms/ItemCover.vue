<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from '@/components/atoms/AppIcon.vue'
import { contentTypeGlyph } from '@/constants/contentTypes'

const props = defineProps<{
  coverUrl?: string | null
  contentType: string
  title: string
}>()

// The null cover_url is the primary signal; a cached file can still go missing
// between the payload and the request, so a 404 lands in the same state.
const failed = ref(false)
watch(
  () => props.coverUrl,
  () => {
    failed.value = false
  },
)

const source = computed(() => (failed.value ? null : props.coverUrl || null))
const glyph = computed(() => contentTypeGlyph(props.contentType))
</script>

<template>
  <span class="cover-art" :class="{ 'cover-art--none': !source }">
    <img v-if="source" :src="source" alt="" loading="lazy" @error="failed = true" />
    <template v-else>
      <AppIcon :name="glyph" :size="20" />
      <span class="sr-only">No cover art for {{ title }}</span>
    </template>
  </span>
</template>
