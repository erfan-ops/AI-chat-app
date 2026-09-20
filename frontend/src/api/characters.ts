import { apiRequest } from './client'
import type { Character, CharacterCreateRequest } from '../types/api'

export function listCharacters(): Promise<Character[]> {
  return apiRequest<Character[]>('/characters')
}

export function getCharacter(characterId: number): Promise<Character> {
  return apiRequest<Character>(`/characters/${characterId}`)
}

export function createCharacter(request: CharacterCreateRequest): Promise<Character> {
  return apiRequest<Character>('/characters', { method: 'POST', body: request })
}
