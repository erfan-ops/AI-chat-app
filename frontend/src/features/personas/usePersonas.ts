import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createPersona, listPersonas } from '../../api/personas'
import type { PersonaCreateRequest } from '../../types/api'

export const personasQueryKey = ['personas'] as const

/** The user's personas, cached for the session. */
export function usePersonas() {
  return useQuery({
    queryKey: personasQueryKey,
    queryFn: listPersonas,
  })
}

export function useCreatePersona() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (request: PersonaCreateRequest) => createPersona(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: personasQueryKey })
    },
  })
}
