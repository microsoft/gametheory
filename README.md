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

## Current milestone: authoring, preparation, and controlled runs

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
- An administrator Settings page for classified environments, execution policy,
  and explicit operator/reviewer grants. Production approval is mandatory;
  nonproduction approval is configurable and off by default.
- A separate, opt-in SQL/REST executor with pinned runs, manual/scheduled starts,
  pause/stop, durable evidence, objective findings, and explicit recovery.
- Guided run setup forms, a non-mutating check before creating a run, and a launch
  checklist that explains every blocker and who can resolve it.
- An opt-in run-check assistant (off by default): operators describe checks in plain
  words, then review suggested watches, goal rules, and undo bindings in the forms.
  It only suggests; it never creates, authorizes, or starts a run.

**Execution is disabled by default and requires separate deployment configuration
and authorized target readiness.** Publishing a revision, registering a connection, or approving a
preparation does not authorize execution. Preparation approval cannot later be
promoted into execution approval. Administrators configure environment policy;
deployment operators separately authorize concrete targets and identities. Graph
sending, MCP, and arbitrary SQL/HTTP execution remain unavailable.

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

- [Roadmap and current status](docs/roadmap.md)
- [Architecture and behavioral contracts](docs/architecture.md)
- [Generic preparation and operation contracts](docs/preparation-contracts.md)
- [Environment policies and exercise execution](docs/execution.md)
- [Commercial deployment preparation and validation gates](docs/deployment.md)
- [Live acceptance runbook and validation environment](docs/live-acceptance.md)
- [Flood-response pilot and implementation handoff](docs/flood-response-pilot-plan.md)
- [Security reporting](SECURITY.md)

Infrastructure templates and CI checks are included. No Azure resources are
provisioned automatically. Local/fixture checks do not establish live tenant,
model, or production deployment readiness.

## Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit
[Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the
[Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more
information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or
comments. See [SUPPORT.md](SUPPORT.md) for how to get help.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of
Microsoft trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion
or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those
third-party's policies.

## License

Licensed under the [MIT License](LICENSE).
