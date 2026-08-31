import { apiRequest } from './client'
import type { AIModel } from '../types/api'

/** Models available for conversations; used to pick a conversation's model. */
export function listModels(): Promise<AIModel[]> {
  return apiRequest<AIModel[]>('/models')
}
