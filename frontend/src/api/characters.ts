import { apiRequest } from './client'
import type { Character } from '../types/api'

export function listCharacters(): Promise<Character[]> {
  return apiRequest<Character[]>('/characters')
}

export function getCharacter(characterId: number): Promise<Character> {
  return apiRequest<Character>(`/characters/${characterId}`)
}
