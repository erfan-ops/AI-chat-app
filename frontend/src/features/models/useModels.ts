import { useQuery } from '@tanstack/react-query'
import { listModels } from '../../api/models'

export const modelsQueryKey = ['models'] as const

/** Active AI models, cached for the session. */
export function useModels() {
  return useQuery({
    queryKey: modelsQueryKey,
    queryFn: listModels,
  })
}
