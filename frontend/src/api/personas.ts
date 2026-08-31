import { apiRequest } from './client'
import type { Persona, PersonaCreateRequest } from '../types/api'

export function listPersonas(): Promise<Persona[]> {
  return apiRequest<Persona[]>('/personas')
}

export function createPersona(request: PersonaCreateRequest): Promise<Persona> {
  return apiRequest<Persona>('/personas', { method: 'POST', body: request })
}
