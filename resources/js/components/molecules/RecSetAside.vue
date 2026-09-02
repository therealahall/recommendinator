<script setup lang="ts">
import AppIcon from '@/components/atoms/AppIcon.vue'

defineProps<{
  dbId: number
  title: string
  rank: number
}>()

const emit = defineEmits<{
  undo: [dbId: number]
}>()
</script>

<template>
  <div class="rec-aside" :data-testid="`ignored-row-${dbId}`">
    <div class="rec-gutter">
      <span class="rec-rank" aria-hidden="true">{{ rank }}</span>
    </div>
    <p class="rec-aside-body">
      <span class="badge"><AppIcon name="close" />Set aside</span>
      <span><b>{{ title }}</b> — you will not be shown this again.</span>
      <span class="rec-aside-spacer" />
      <button
        type="button"
        class="btn btn-ignore"
        :data-testid="`undo-ignore-${dbId}`"
        :aria-label="`Undo ignoring ${title}`"
        @click="emit('undo', dbId)"
      >Undo</button>
    </p>
  </div>
</template>
