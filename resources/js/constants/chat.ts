export interface ChatSuggestion {
  text: string
  contentType: string
  label: string
}

export const SUGGESTIONS: ChatSuggestion[] = [
  { text: 'What game do you think will be my next obsession?', contentType: 'video_game', label: 'What game will be my next obsession?' },
  { text: "What book do you think I'll get lost in next?", contentType: 'book', label: 'What book will I get lost in next?' },
  { text: 'What movie should I watch this weekend?', contentType: 'movie', label: 'What movie should I watch this weekend?' },
  { text: 'What TV show should I binge next?', contentType: 'tv_show', label: 'What TV show should I binge next?' },
]
