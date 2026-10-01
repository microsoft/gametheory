# Live acceptance runbook

This runbook turns the [live acceptance](deployment.md#live-acceptance) checklist into
repeatable steps for one deployed studio, plus a disposable validation environment for
the failure cases that would otherwise mean breaking that studio. Nothing here approves
provisioning, Entra changes, or cost. Get the deployment owner's approval for each step
that changes resources or registrations.

Two targets:

- **The studio**: checks against the real deployment that only add synthetic records.
  Run them with `scripts/acceptance/Invoke-StudioAcceptance.ps1` and two browser checks.
- **A validation environment**: a second studio deployed from the same template and
  image digests into its own resource group, plus one long-lived deployment per fault.
  `azd up` creates it and `azd down --purge` removes all of it, so faults can be
  repeated without touching the studio.

| ID  | Proves                                                                                                  | Target     | How                       |
| --- | ------------------------------------------------------------------------------------------------------- | ---------- | ------------------------- |
| S1  | Real Entra sign-in; an account from another tenant is rejected                                          | Studio     | Browser                   |
| A   | A real planning proposal is reviewed and applied in the studio UI                                       | Studio     | Browser                   |
| S2  | Missing, malformed, wrong-audience, wrong-tenant, and expired tokens get 401                            | Studio     | Studio runner             |
| S3  | Authoring, conditional saves, publication, private assets, idempotent planning, an applied proposal     | Studio     | Studio runner             |
| S4  | Non-members see nothing, viewers cannot save, and removal revokes access                                | Studio     | Studio runner             |
| S5  | Records, revisions, and asset content persist across an application restart                             | Studio     | Studio runner             |
| S6  | SQL, Blob, Scheduler, and Foundry resolve privately; a real proposal; a managed Scheduler activity      | Studio     | `validate-dependencies`   |
| S7  | A private Blob round trip with the API identity                                                         | Studio     | `validate-blob`           |
| S8  | Logs correlate requests and orchestration instances, without prompts, asset content, or tokens          | Studio     | Studio runner (KQL)       |
| F0  | The validation studio passes the S3 flow                                                                | Validation | Scenario runner           |
| F1  | A worker killed mid-activity resumes, and exactly one result is persisted                               | Validation | `test-worker-restart` job |
| F2  | With SQL unavailable, data requests get 503 with a request ID that matches the API log                  | Validation | Scenario runner           |
| F3  | With Blob unavailable, uploads and reads fail closed with 503 and no usable asset is published          | Validation | Scenario runner           |
| F4  | With the Scheduler unavailable, a request stays visibly queued, then completes exactly once             | Validation | Scenario runner           |
| F5  | When the model denies the worker, the request fails after bounded retries, with no provider body logged | Validation | Scenario runner           |

## Prerequisites

- PowerShell 7.2 or later, Azure CLI with the `containerapp` extension, and, for the
  validation environment, the Azure Developer CLI.
- Python 3.12 with the backend dev dependencies from
  [development setup](development.md); the runners use the repository's `.venv` or
  `backend/.venv`, or `GT_ACCEPTANCE_PYTHON`.
- Owner, or Contributor plus User Access Administrator, on the studio's resource group,
  and Log Analytics Reader on its workspace.
- For the one-time Entra change below, an Entra role that can update the studio's API
  registration, such as its owner or Application Administrator.
- A studio deployed per [deployment preparation](deployment.md) with planning on and
  execution off, and its first administrator bootstrapped. The runners sign in as that
  administrator.

## One-time setup for a studio

1. **Let Azure CLI sign the tests in.** The API accepts only Entra tokens for its own
   scope. Preauthorize Azure CLI on the API registration so
   `az account get-access-token --scope api://<api-app-id>/access_as_user` works without
   a consent prompt:

   ```powershell
   pwsh ./scripts/acceptance/Grant-AzureCliAccess.ps1 -ApiAppId <api-app-id>
   ```

   Any signed-in user in the tenant can then request a token for the API from Azure
   CLI. Workspace and administrator authorization in the application still apply. Undo
   it with `-Remove`.

2. **Optional second account for S4.** Use a member of the studio's tenant who is not
   an organization administrator in the studio. Sign it in to a separate Azure CLI
   profile, so your own sign-in is unaffected:

   ```powershell
   $env:AZURE_CONFIG_DIR = "$HOME/.azure-acceptance-member"
   az login --tenant <studio-tenant-id>
   Remove-Item Env:AZURE_CONFIG_DIR
   ```

3. **Optional other tenant for S2.** Any tenant where an account in your Azure CLI is
   signed in provides the wrong-tenant token. It can be a different account, added with
   `az login --tenant <other-tenant-id>`, as long as that tenant has a subscription the
   CLI lists; otherwise the default account must be able to sign in there.

4. **API logs.** The main template sends the web app's console, HTTP, and platform logs
   to the deployment's Log Analytics workspace. For a studio deployed before that
   change, redeploy `infra/main.bicep` with the parameters of its last deployment. Run
   `az deployment group what-if` first: expect the diagnostic setting and new outputs,
   with no change to the web app or worker images. Reattach any governance network
   security groups right after the redeployment.

5. **Probe jobs.** Publish the validation image to the studio's registry and deploy
   `infra/probes.bicep`, which adds the `validate-dependencies` and `validate-blob` manual
   jobs and their isolated `validation` task hub:

   ```powershell
   pwsh ./scripts/acceptance/Deploy-ProbeJobs.ps1 -ResourceGroup <studio-resource-group>
   ```

   The probes have their own template because a Consumption Scheduler allows five task
   hubs, and deployment validation counts each hub a template declares on top of the
   existing ones. If earlier probe jobs or a `validation` hub grant were created by hand,
   delete that hand-made role assignment first; otherwise the template's grant fails as
   a duplicate. The hand-made jobs and hub are adopted in place.

## Run the studio checks

```powershell
pwsh ./scripts/acceptance/Invoke-StudioAcceptance.ps1 -ResourceGroup <studio-resource-group> `
    -OtherTenant <other-tenant-id> -MemberConfigDirectory "$HOME/.azure-acceptance-member" `
    -WaitForTokenExpiry
```

The runner finds the studio from its latest template deployment's outputs (or
`-Deployment`), reads the API scope from `/api/config`, and then:

1. starts the probe jobs (S6, S7) in the background;
2. runs `backend/tests/test_live_api.py` for S2, S3, and S4. Each run creates synthetic
   "Deployment validation" and "Isolation validation" workspaces, which remain as
   records;
3. restarts the web app and the planning worker, then rechecks the S3 records (S5);
4. waits for log ingestion and queries Log Analytics (S8). The worker log must name the
   S3 orchestration instance, `planning-<request-id>`. The API's console and HTTP logs
   must record the run's requests. No application log may contain the S3 prompt, the
   asset content, or anything token-shaped (`eyJ`, `Bearer `);
5. with `-WaitForTokenExpiry`, keeps its first token until it expires, 60 to 90 minutes
   after issue, and proves the API refuses it.

Optional inputs that are left out show up as skipped cases in the summary. The first
ingestion into new App Service log tables can take about 20 minutes. If S8 fails right
after enabling logs, rerun with `-SkipRestart -SkipProbes`. The summary and JUnit files
go to `.acceptance/studio/<timestamp>/`.

### Browser checks

- **S1.** Open the studio, sign in as the administrator, and confirm the library loads.
  In a private window, sign in with an account from another tenant; Entra must refuse it.
- **A (reviewed proposal).** Open a scenario, save it, and enter a planning instruction
  that adds a measurable objective. Choose **Request proposal**. When the proposal
  arrives, open the review, compare the draft with the proposal, and choose
  **Apply reviewed proposal**. Confirm "Proposal applied and saved as a new draft
  version." Record the request ID and the time to a proposal. If the proposal is
  truncated, raise `plannerMaxOutputTokens`.

## Validation environment

`validation/` is an azd project with its own resource group, named
`rg-gametheory-validation-<environment>`. It contains:

- a full studio from `infra/main.bicep` with its own SQL server, storage, Scheduler,
  Foundry project, Container Apps environment, App Service plan, registry, and Log
  Analytics workspace, plus its probe jobs from `infra/probes.bicep`. It runs the
  studio's exact API and worker image digests when `STUDIO_RESOURCE_GROUP` is set;
  otherwise it builds images from your checkout;
- one long-lived deployment per fault, each with its own database and task hub. The
  fault hubs live on two more private Schedulers, with at most two hubs on each, so
  every `azd provision` passes the Consumption task hub check:

  | Scenario           | Deployments                                                        | Deliberate fault                                 |
  | ------------------ | ------------------------------------------------------------------ | ------------------------------------------------ |
  | `worker-restart`   | `test-worker-restart` job, `gametheory_test`, `restart-test`       | The test kills its fixture worker mid-activity   |
  | `sql-outage`       | `fault-sql-api`                                                    | Its SQL URL names a database that does not exist |
  | `blob-outage`      | `fault-blob-api`, `gametheory_blob_outage`                         | Its Blob URL is a host that never resolves       |
  | `scheduler-outage` | `fault-scheduler-api` and `-worker`, `gametheory_scheduler_outage` | The worker's Scheduler endpoint never resolves   |
  | `model-denied`     | `fault-model-api` and `-worker`, `gametheory_model_denied`         | The worker's identity has no Foundry role        |

- `setup-databases`, a job run as a disposable SQL administrator identity. It applies
  migrations, grants table-scoped runtime permissions, and registers you as the first
  administrator in every database. It is safe to rerun.

Faulty hosts use the reserved `.invalid` domain, so no fault reaches a real service.
Scenario APIs have public, Entra-protected ingress and scale to zero. Scenario workers
keep one replica. The environment has ongoing cost while it exists, mostly the workers,
SQL databases, private endpoints, and model capacity. `azd down --purge` removes it.

### Deploy

```powershell
cd validation
azd config set auth.useAzCliAuth true   # the hooks use az; share one sign-in
azd env new <environment> --subscription <subscription-id> --location <region>
azd env set STUDIO_RESOURCE_GROUP <studio-resource-group>   # optional; see below
azd up
```

| Setting                                          | Purpose                                                                                                                          |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `STUDIO_RESOURCE_GROUP`                          | Import the studio's running image digests, and read its Scheduler private DNS zone name                                          |
| `STUDIO_SUBSCRIPTION_ID`                         | The studio's subscription, when it differs from the validation environment's                                                     |
| `SCHEDULER_PRIVATE_DNS_ZONE`                     | Required without `STUDIO_RESOURCE_GROUP`; take it from the Scheduler's private link metadata                                     |
| `PLANNING_MODEL_CAPACITY`                        | Model capacity units (default 20). Check quota; a different subscription keeps quota separate                                    |
| `VALIDATION_SCENARIOS`                           | Comma-separated subset of the scenarios above (default: all)                                                                     |
| `VALIDATION_ORGANIZATION_NAME`                   | Organization name bootstrapped into each database                                                                                |
| `VALIDATION_API_APP_ID`, `VALIDATION_SPA_APP_ID` | Existing registrations to use instead of dedicated ones; their redirect URIs and Azure CLI preauthorization stay yours to manage |

The hooks are PowerShell and safe to rerun:

- `preprovision` creates the resource group and its registry, imports or builds the API
  and worker images, and builds the validation image with ACR Tasks. It then creates
  dedicated SPA and API registrations, named `gametheory-validation-<environment>`,
  that preauthorize the SPA and Azure CLI.
- `postprovision` runs `setup-databases`, registers each app's origin as an SPA
  redirect URI, and prints the URLs.
- `postdown` deletes the registrations the hooks created.

Applications start before `setup-databases` finishes, so they log database errors for a
few minutes on the first deployment. That is expected in this environment only.

### Run the fault scenarios

```powershell
pwsh ./validation/scripts/Invoke-ValidationScenario.ps1                      # everything
pwsh ./validation/scripts/Invoke-ValidationScenario.ps1 -Scenario blob-outage
```

The runner starts the restart and probe jobs, then runs F0 and F2 through F5. Each
fault's pass criteria come from the application's own failure paths:

- **F2:** `/api/me` and `/api/workspaces` return 503 "Application database is
  unavailable…". The body's `request_id` equals the `X-Request-ID` header.
  `/api/health` and `/api/config` still respond. The API log has "SQL operation failed"
  with the same correlation ID.
- **F3:** An upload returns 503 "Asset upload failed; no usable asset was published", and
  the staged record is listed but not downloadable. The runner then briefly points the
  app at real storage to seed one asset, and restores the fault. Reading that asset
  then returns 503 "Asset storage is unavailable", and it stays `ready`.
- **F4:** A planning request stays `queued` with "Scheduler dispatch unavailable; retry
  is scheduled." The runner points the worker at the real Scheduler; the same request
  reaches `proposed` exactly once. The runner then restores the fault. The worker log
  names the request in "Scheduler dispatch failed".
- **F5:** The request ends `failed` with "Planning failed after bounded retries…" and has
  no proposal. The worker log names the request in "Planning activity failed". Neither
  the provider's error text nor the prompt appears in the log.
- **F1:** The job runs `test_scheduler_integration.py -k worker_crash` against the
  managed Scheduler, for both planning and run-check suggestions.

Every fault the runner changes is restored in a `finally` block, even when a check
fails. Evidence goes to `.acceptance/validation/<environment>/<timestamp>/`.

### Remove

```powershell
cd validation
azd down --purge
```

`--purge` also purges the soft-deleted Foundry account, which otherwise keeps its name
and quota.

## Recording results

- Evidence folders name tenant resources. They are gitignored; never commit them.
- Record tenant-neutral outcomes in the [roadmap](roadmap.md): the date, the commit, what
  passed, and any limitation.
- The runners never print tokens, and nothing in the evidence contains one. If a token is
  ever exposed, revoke the user's sessions.

## Troubleshooting

| Symptom                                                                                            | Cause and fix                                                                                                                                          |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `TokenCreatedWithOutdatedPolicies` from Graph                                                      | Continuous access evaluation wants a fresh sign-in: run `az login` again                                                                               |
| `AuthorizationFailed`, `InsufficientAccessError` from log queries, or a registry `Username` prompt | Your Azure role is missing, or a time-bound (PIM) activation ended; services notice at different times. Reactivate the role and rerun                  |
| A probe or restart job fails but its dependencies work                                             | The jobs set `GT_TEST_REQUIRE_PASS`, so a skipped test fails them; the job's console log names the skip reason                                         |
| `AADSTS65001` when getting an API token                                                            | Azure CLI is not preauthorized for that API registration; run `Grant-AzureCliAccess.ps1`                                                               |
| `RoleAssignmentExists` when deploying the probe jobs                                               | A hand-made `validation` hub grant exists; delete it, then deploy again                                                                                |
| `QuotaExceeded` for a task hub                                                                     | A template declares too many hubs for its Consumption Scheduler; see the deployment notes                                                              |
| Subnets lost their network security groups                                                         | Redeployments detach governance-attached groups; reattach them                                                                                         |
| `InsufficientQuota` for the model                                                                  | Lower `PLANNING_MODEL_CAPACITY` or use a subscription with separate quota                                                                              |
| `ManagedEnvironmentCapacityHeavyUsageError`                                                        | Choose another approved region for the validation environment                                                                                          |
| `az acr build` is blocked by policy                                                                | Build the `validation` target with Docker, push it, and pass it to `Deploy-ProbeJobs.ps1 -ValidationImage`; the validation environment needs ACR Tasks |
| A new job fails to pull its image                                                                  | Registry role assignments take minutes to propagate; `postprovision` retries setup three times                                                         |
| S8 counts are 0 right after enabling App Service logs                                              | First ingestion into new tables is slow; rerun with `-SkipRestart -SkipProbes`                                                                         |
