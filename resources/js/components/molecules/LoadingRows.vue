<script setup lang="ts">
withDefaults(defineProps<{ rows?: number }>(), { rows: 3 })
</script>

<template>
  <div class="loading-rows" aria-hidden="true">
    <!-- Decorative: the page's own status region says a run is in flight. -->
    <div v-for="row in rows" :key="row" class="loading-row">
      <span class="loading-art" />
      <div class="loading-lines">
        <i v-for="line in row === 1 ? 4 : 3" :key="line" />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* The rows are the unit, so the caller's own container lays them out: one
   column down the ranked list, a cell each in the library grid. */
.loading-rows {
  display: contents;
}

/* Every measure below is the card's own, so the list arrives into the shape
   already drawn rather than pushing it around. */
.loading-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--space-5);
  padding: var(--space-6) var(--space-5);
}

.loading-row + .loading-row {
  box-shadow: inset 0 1px 0 var(--border-subtle);
}

.loading-art {
  width: calc(var(--cover-height) / 1.5);
  height: var(--cover-height);
  margin-left: var(--space-4);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
  animation: breathe 1.9s ease-in-out infinite;
}

.loading-lines i {
  display: block;
  height: 1px;
  background: var(--border-default);
  animation: breathe 1.9s ease-in-out infinite;
}

.loading-lines i + i {
  margin-top: var(--space-5);
}

.loading-lines i:nth-child(1) {
  width: 22%;
}

.loading-lines i:nth-child(2) {
  width: 56%;
  animation-delay: 130ms;
}

.loading-lines i:nth-child(3) {
  width: 71%;
  animation-delay: 260ms;
}

.loading-lines i:nth-child(4) {
  width: 38%;
  animation-delay: 390ms;
}

/* Out of phase, so the row reads as one thing breathing rather than four. */
@keyframes breathe {
  0%,
  100% {
    opacity: 0.4;
  }
  50% {
    opacity: 1;
  }
}

@media (max-width: 640px) {
  .loading-row {
    gap: var(--space-4);
    padding: var(--space-4);
  }

  .loading-art {
    margin-left: 0;
  }
}
</style>
