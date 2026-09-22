import { expectTypeOf, it } from 'vitest'
import type { paths } from './api.generated'

type ConfigurationHeaders = NonNullable<
  paths['/api/workspaces/{wid}/connections/{cid}/configurations']['post']['parameters']['header']
>
type BoardCreationHeaders = NonNullable<
  paths['/api/workspaces/{wid}/boards']['post']['parameters']['header']
>
type GrantHeaders = NonNullable<
  paths['/api/workspaces/{wid}/approvers']['put']['parameters']['header']
>
type RevokeGrantHeaders = NonNullable<
  paths['/api/workspaces/{wid}/approvers/{object_id}']['delete']['parameters']['header']
>

it('keeps append-only creation and grant management free of invented collection preconditions', () => {
  expectTypeOf<ConfigurationHeaders>().toEqualTypeOf<never>()
  expectTypeOf<BoardCreationHeaders>().toEqualTypeOf<never>()
  expectTypeOf<GrantHeaders>().toEqualTypeOf<never>()
  expectTypeOf<RevokeGrantHeaders>().toEqualTypeOf<never>()
})

it('declares preconditions for mutable boards and individual configuration withdrawal', () => {
  expectTypeOf<
    NonNullable<paths['/api/workspaces/{wid}/boards/{bid}']['put']['parameters']['header']>
  >().toHaveProperty('If-Match')
  expectTypeOf<
    NonNullable<
      paths['/api/workspaces/{wid}/boards/{bid}/previews']['post']['parameters']['header']
    >
  >().toHaveProperty('If-Match')
  expectTypeOf<
    NonNullable<
      paths['/api/workspaces/{wid}/boards/{bid}/approvals']['post']['parameters']['header']
    >
  >().toHaveProperty('If-Match')
  expectTypeOf<
    NonNullable<
      paths['/api/workspaces/{wid}/boards/{bid}/approvals/{approval_id}/revoke']['post']['parameters']['header']
    >
  >().toHaveProperty('If-Match')
  expectTypeOf<
    NonNullable<
      paths['/api/workspaces/{wid}/connections/{cid}/configurations/{config_id}/withdraw']['post']['parameters']['header']
    >
  >().toHaveProperty('If-Match')
})
