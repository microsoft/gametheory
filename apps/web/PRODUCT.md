# Game Theory

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Scenario owners and workspace editors author shared exercise plans. Viewers inspect
them without editing. Organization administrators manage connection configuration
and explicit workspace approver grants. A separately assigned, non-contributing
approver reviews preparation snapshots.
Administrators configure environment execution policy in Settings. Explicit run
operators use the separate run workflow; independent execution reviewers approve
production and any nonproduction environments whose policy requires review.

## Product Purpose

Game Theory is an organization's exercise control plane. Participants work in their
usual tools, not in a Game Theory participant portal.

## Capabilities and Constraints

- Preserve document-first scenario authoring, planning proposals, assets, flow
  editing, conflict-safe saves, and immutable published revisions.
- Users create inventory, register external operation catalogs, author scenarios,
  publish revisions, and create generic preparation boards through ordinary UI.
- Boards pin published inputs; static previews freeze exact preparation versions.
  Registration and preview do not contact targets or establish live readiness.
- Preparation approval is not authorization to execute. A separate opt-in SQL/REST
  executor uses current policy and operator-approved target/readiness records.
  Execution is disabled by default; there are no fabricated approvals or outcomes.
- Production always requires independent execution approval; administrators choose
  whether nonproduction environments require it. Unknown environments cannot run.
- Run controls expose progress, waiting, failures, unknown effects, evidence,
  assessment, and explicit recovery. Stop does not undo accepted actions; manual
  outcome reports are labelled rather than represented as verified success.
- Organization administration, ownership, and editing do not confer approval.
  Board creators and preparation contributors cannot approve their own work.
- Catalogs describe restricted operations. The product accepts no arbitrary SQL,
  scripts, unrestricted HTTP requests, credentials, or remote schema references.
- Graph sending, MCP execution, live target provisioning, and production deployment
  remain outside the implemented execution milestone.
- Product code has no scenario installer, predefined exercise, domain-specific
  connector, or built-in external-system workflow.

## Brand Commitments

Preserve the established Game Theory name and mineral/ocean studio identity,
including the existing light/dark theme and glass component system.

## Evidence on Hand

The repository README, architecture, approved implementation plan, and versioned
preparation contracts are authoritative. Local fixtures are explicitly test-only;
they do not demonstrate live permissions, connectivity, delivery, or execution.

## Accessibility & Inclusion

Keyboard access, clear labels, responsive layouts, both themes, honest failure and
read-only states, and preservation of unsaved input are required.
