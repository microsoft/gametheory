# Game Theory

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Scenario owners and workspace editors author shared exercise plans. Viewers inspect
them without editing. Organization administrators manage connection configuration
and explicit workspace approver grants. A separately assigned, non-contributing
approver reviews preparation snapshots.

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
- Preparation approval is not authorization to execute. Execution remains disabled;
  there are no simulated runs, invented outcomes, or fabricated execution evidence.
- Organization administration, ownership, and editing do not confer approval.
  Board creators and preparation contributors cannot approve their own work.
- Catalogs describe restricted operations. The product accepts no arbitrary SQL,
  scripts, unrestricted HTTP requests, credentials, or remote schema references.
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
