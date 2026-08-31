import { useQuery } from '@tanstack/react-query'
import { listCharacters } from '../../api/characters'
import type { Character } from '../../types/api'

export const charactersQueryKey = ['characters'] as const

/** Active characters (personas), cached for the session. */
export function useCharacters() {
  return useQuery({
    queryKey: charactersQueryKey,
    queryFn: listCharacters,
  })
}

/** Resolves a character's full record (e.g. for the avatar) by id, reusing the
 *  shared characters cache. `ConversationRead` only embeds a `{id, name}` brief. */
export function useCharacter(characterId: number | null | undefined): Character | undefined {
  const query = useCharacters()
  if (characterId == null) return undefined
  return query.data?.find((character) => character.id === characterId)
}
