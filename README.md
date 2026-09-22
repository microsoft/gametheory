# Game Theory

Game Theory helps organizations design realistic exercises, coordinate activity
across their operational systems, and evaluate outcomes using evidence. Participants
work in their everyday tools; Game Theory is the scenario-owner control plane.

## Purpose

- Create configurable scenarios to test and evaluate responses to complex situations.
- Support both **time-based** and **manual** triggers.
- Deliver simulated data across multiple systems for authenticity.
- Facilitate coordination between AI and real-world applications.

## Scope & Use Cases

- **Disaster preparedness** simulations for federal agencies.
- Evaluation of training effectiveness and personnel response times.
- Multi-team coordinated activities and exercises.
- AI-assisted scenario execution and monitoring.

## Current milestone: authoring and exercise preparation

The new document-first, mineral/ocean studio provides:

- Entra sign-in, organization/workspace authorization, and shared drafts.
- Rich plans, objectives and evidence criteria, React Flow editing, and generated
  Mermaid source.
- Private versioned assets, environment-labeled connection inventory, comments,
  conflict-safe saves, and immutable published revisions.
- Persisted Agent Framework planning requests and explicitly reviewed proposals,
  coordinated by Durable Task Scheduler and Python Container Apps workers.
- Generic, versioned connection configuration and user-registered operation
  descriptions, without contacting the described targets.
- Game boards pinned to published revisions, static preparation previews, and
  digest-bound preparation review by separately granted approvers.

**This milestone does not execute exercises or write to connected organizational
systems.** Publishing a revision, registering a connection, or approving a
preparation does not authorize execution. Preparation approval cannot later be
promoted into execution approval. Connection configuration does not activate SQL,
REST, Microsoft Graph, or MCP access.

An [independent flood exercise system](exercises/flood-response/README.md) provides
synthetic operational data, an API, a separate response UI, and scenario material.
It is not part of the Game Theory runtime. Configure its connections and author
its scenario through the same UI as any other system: there is no starter kit,
scenario installer, or hidden application seeding path.

## Stack

React 18, TypeScript, Vite, Tailwind, and a custom glass design system; Python 3.12,
FastAPI, Pydantic, SQLAlchemy/Alembic, Azure SQL, and Blob Storage. Commercial hosting
uses App Service for web/API and Container Apps with Durable Task Scheduler for
planning work. Government/custom execution alternatives are deferred.

## Getting started

See [development setup](docs/development.md) for dependencies, environment settings,
Entra registrations, SQL migrations, administrator bootstrap, and local commands.
Missing configuration produces explicit errors, not an authentication bypass or
simulated data.

- [Architecture and behavioral contracts](docs/architecture.md)
- [Generic preparation and operation contracts](docs/preparation-contracts.md)
- [Commercial deployment preparation and validation gates](docs/deployment.md)
- [Flood-response pilot and implementation handoff](docs/flood-response-pilot-plan.md)
- [Security reporting](SECURITY.md)

Infrastructure templates and CI checks are included. No Azure resources are
provisioned automatically. Local/fixture checks do not establish live tenant,
model, or production deployment readiness.
