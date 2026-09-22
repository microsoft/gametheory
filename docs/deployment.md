# Commercial deployment preparation

**No deployment is performed by development setup or CI.** Obtain approval for
subscription, region, cost, networking, identities, and model use before provisioning.
Government/custom runtime deployment is deferred.

## What the template contains

`infra/main.bicep` describes:

- Entra-only Azure SQL and private Blob storage with private endpoints.
- Managed Durable Task Scheduler (Consumption SKU) and task-hub-scoped worker access.
- A virtual network with distinct App Service, Container Apps, and private-endpoint
  subnets; private DNS links.
- Separate API and worker managed identities, an ACR registry with admin access
  disabled, App Service, a Container Apps environment, and Log Analytics.
- Application deployments gated by `deployApplications`; planning workers additionally
  require `enablePlanning`.

The first template deployment should set `deployApplications=false`. It creates
infrastructure/identities but does not start an unconfigured application. Supply
reviewed container images by immutable digest for the subsequent application
deployment. The API and worker images must be pushed to the created registry.

The template is a greenfield starting point, not an agency network landing zone.
Review its address ranges, SKUs, retention, egress, registry exposure, regional
availability, and private DNS ownership. SQL, Blob, and Scheduler public data-plane
access are disabled. Registry access is public but identity-authenticated; organizations
requiring a private registry must adapt the registry/network design before deployment.

The Scheduler private DNS zone name is an explicit input, because it must come
from the target service's supported private-link configuration, not a guessed DNS
suffix. Obtain it from the `privateLinkResources` metadata of an approved scheduler
or the current service configuration guidance. If bootstrapping a new service,
establish its approved name/region and inspect that metadata before completing
the combined network deployment. Compilation does not verify private DNS.
Use `schedulerName` to adopt that same scheduler into the combined deployment.

For an isolated validation deployment, `deployFoundry=true` additionally creates
a private Foundry account/project and a regional GPT-4.1-mini Standard deployment
(50 capacity units). `planningModelSku=DataZoneStandard` can instead keep inference
within the deployment's data zone (US for a US-region account). Global routing is
not an option in this template. Check actual model-capacity results as well as
catalog entries and quota; a listed SKU does not prove regional availability. The
three DNS zones come from commercial account private-link metadata; the worker
receives Foundry User only on this project. No hosted Agent Service or external
tools are provisioned. Project, model, and private endpoint creation are serialized
because concurrent changes to a Cognitive Services account can conflict.

## Required operator inputs and gates

1. Validate commercial region/SKU support for Scheduler, Container Apps, App Service,
   SQL, and the chosen model. Confirm service quotas and consumption budgets.
   Catalog support does not guarantee capacity. Deploy `infra/compute-network.bicep`
   first in the intended resource group, using the same `location` and `namePrefix`
   as the main template. This verifies actual Container Apps environment allocation;
   `infra/main.bicep` reuses those resources. If allocation reports
   `ManagedEnvironmentCapacityHeavyUsageError`, choose another approved region
   before provisioning the rest. Remove task-owned failed preflight resources
   after preserving any required images or data.
2. Supply tenant, SPA/API registration settings, SQL administrator principal, approved
   Scheduler private DNS zone, Foundry project endpoint, and model deployment.
   Registrations, consent, and guest identities are not created by the template.
   Models are created only when opting into the dedicated Foundry module.
   `sqlAdminId`, `sqlAdminName`, and `sqlAdminPrincipalType` accept an existing
   group (default), user, or application. A dedicated operator managed identity
   (`Application`) can run setup in a manual Container Apps job without exposing
   SQL or copying a user's token. Never attach this identity to the API or worker.
   For `Application`, supply its **client ID** as `sqlAdminId`, not its service
   principal object ID. User/group administrators use their object ID.
   Check the SQL location capabilities and complete an actual SQL-server preflight;
   `sqlServerName` lets the combined template adopt that same verified server.
3. Establish operator access from the private network (VPN, jump host, or appropriately
   connected runner). Do not temporarily expose SQL/Blob/Scheduler just to run setup.
4. Build and push the images:

   ```bash
   docker build --target api -t YOUR_REGISTRY/gametheory-api:YOUR_TAG .
   docker build --target worker -t YOUR_REGISTRY/gametheory-worker:YOUR_TAG .
   ```

   Record the resulting digests for deployment. CI builds but never pushes images.

5. As the SQL operator, apply Alembic migrations and create database users for the
   API and worker managed identities. Azure RBAC alone does not grant SQL data access.
   Resolving Entra principals for SQL user creation may require tenant/admin setup;
   do not give the runtime identities directory-write permissions.
6. Grant table-scoped runtime permissions. The API needs authoring table access;
   the worker needs authorization/context reads, planning/dispatch updates, and audit
   insertion. Neither needs DDL or bootstrap administrator-write privileges.
   Give runtime identities only SELECT/INSERT on immutable revision and audit tables,
   not UPDATE/DELETE. Use a separate operator for cleanup and migrations.
   On a fresh application database, the operator command below creates contained
   users using managed-identity **client IDs** without Microsoft Graph permissions:

   ```bash
   gametheory database-grants --api-client-id API_CLIENT_UUID --worker-client-id WORKER_CLIENT_UUID
   ```

   It refuses identity mismatches. It adds table grants, not database-owner roles,
   and does not revoke pre-existing grants; audit existing database users separately.

7. Bootstrap the first organization administrator explicitly.
8. Grant the worker appropriate native Foundry project/model access. The template
   grants Scheduler permissions and, for the optional dedicated project, Foundry
   permissions. Existing projects need a separate grant. Verify actual inference
   using the selected package/model combination before enabling planning.
   A private Foundry project also requires worker-network connectivity and private
   DNS configuration; this template does not modify an existing project's network.
9. Deploy images, configure the production SPA redirect origin, and verify token
   audience/scope and workspace denial cases. Start with planning disabled until
   SQL, identity, network, and model checks pass.

The application contains no runtime secrets that require Key Vault in this milestone:
cloud services use identities and connection inventory contains no credentials.
Introduce secret references and vault policies when actual integrations are activated,
not a placeholder vault with unnecessary grants.

## Live acceptance

Use authorized disposable application data to verify:

- Real Entra sign-in and denial of wrong-tenant/audience/expired tokens.
- Workspace isolation, conditional saves, publication, and private asset retrieval.
- Durable request dispatch, real model proposal generation, and review/application.
- Worker termination during an activity, resumption, and one accepted result.
- Model denial, unavailable SQL/Blob/Scheduler, and revoked workspace permission.
- DNS resolving SQL, Blob, and Scheduler to private addresses from compute.
- Logs correlated by request/instance IDs without prompts, asset content, or tokens.

The health endpoint reports **process liveness**, not full dependency readiness.
The Docker `validation` target contains test dependencies; production targets do
not include the test suite. Real SQL integration still requires a dedicated
`gametheory_test*` database. For managed Scheduler restart tests, also set
`GT_TEST_SCHEDULER_EMULATOR=false` and use a separate `GT_TEST_SCHEDULER_TASKHUB`
with scoped data-contributor access. These restart tests intentionally use a model
fixture; actual Foundry inference must be checked separately.

`backend/tests/test_live_api.py` checks the complete deployed authoring/planning
flow using an actual authorized owner token, not an authentication override. Set
`GT_LIVE_API_URL`, `GT_LIVE_API_EXPECTED_USER` (the user's Entra object ID), and
`GT_LIVE_API_TOKEN` in the test process environment, then run that test with pytest.
Use an approved Entra client and short-lived token; never commit or print the token.
The test intentionally creates a synthetic workspace/scenario, immutable asset
versions, a comment/revision, and a real model proposal that it explicitly applies.
It verifies stale-save rejection and duplicate-request idempotency. Optional
`GT_LIVE_API_ARTIFACT` records identifiers for follow-up persistence checks after
restarting the application; it contains no credentials.

The single worker replica is an initial baseline, not production HA or scale proof.
Set appropriate scheduler history retention only after defining reconciliation
and recovery expectations; do not purge active or uncertain instances.

Pin/retain `plan_v1` workers for active histories when introducing incompatible
orchestration changes. SQL migrations and application rollout require backward
compatibility review. Test database backup/restore and asset retention independently.

Production approvals, actual organizational-system writes, recovery/compensation,
Foundry agent registration, optional Agent 365 integration, and multi-cloud
validation are outside this milestone.

## Preparation schema rollout and exercise isolation

Preparation introduces additive application tables and additional API-only
table-scoped grants. Apply migrations and review runtime grants using the
separate authorized operator identity before deploying an updated API. The
planning worker must not acquire target-system permissions or treat preparation
records as dispatch intents. Existing `plan_v1` instances and histories retain
their original behavior.

Registration of target metadata, environment classification, operation catalogs,
or approver grants is not cloud authorization. The runtime still advertises
execution as disabled. A preparation approval cannot become execution approval
after a deployment or configuration change.

The independent package in `exercises/flood-response/` has a separate deployment
boundary. The application Docker targets do not bundle it. Its future hosting,
dedicated exercise database, network paths, registrations, managed identities,
SQL permissions, mailbox restrictions, recipients and resource budget require a
new concrete resource proposal and explicit approval. Prior Game Theory deployment
approval does not cover them. Do not run exercise migrations against the
application database or alter private-network policy to make a preview pass.

## Partial deployment and private dependency probes

`ProvisioningDisabled` on SQL server creation is a regional/subscription gate, not
an application error. Obtain an Azure provisioning exception or explicit approval
for a different database region; do not silently relocate data. Until resolved,
leave `GT_SQL_URL` unset and planning disabled if publishing a sign-in-only shell.
Its liveness response does not mean authoring is available. Database migrations,
application administrator bootstrap, and end-to-end authoring remain incomplete.

The `validation` image can run `backend/tests/test_azure_dependencies.py` in a
manual Container Apps job inside the application network, explicitly enabled by
`GT_TEST_AZURE_DEPENDENCIES=true`. Supply the actual service configuration:

- Use the worker identity and `-k "not blob"` for private DNS, real Foundry proposal
  validation, and an actual managed Scheduler activity. Use a separate `validation`
  task hub and grant data-contributor access only on that hub.
- Use the API identity and `-k blob` for an actual private Blob upload/read/delete
  round-trip. The synthetic object has a unique `deployment-validation/` key.

These probes never connect to SQL and are not a substitute for the database-backed
tests or the full authoring/planning lifecycle. Do not run a continuously active
planning worker before database setup. Jobs are manual and have no active replica
after completing.
