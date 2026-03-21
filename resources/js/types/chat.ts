export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  isToolIndicator?: boolean
  toolName?: string
  toolSuccess?: boolean
}
