import re
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Name = Annotated[str, Field(min_length=1, max_length=160, pattern=r".*\S.*")]
Role = Literal["viewer", "editor", "owner"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Named(Contract):
    name: Name


class Position(Contract):
    x: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    y: float = Field(ge=-100000, le=100000, allow_inf_nan=False)


class FlowNode(Contract):
    id: UUID
    kind: Literal["action", "condition", "wait", "approval"]
    label: Name
    detail: str = Field(default="", max_length=4000)
    connection_id: UUID | None = None
    environment_id: UUID | None = None
    position: Position


class FlowEdge(Contract):
    id: UUID
    source: UUID
    target: UUID
    label: str = Field(default="", max_length=160)


class Objective(Contract):
    id: UUID
    title: Name
    criterion: str = Field(min_length=1, max_length=2000)


def empty_document() -> dict[str, JsonValue]:
    return {"type": "doc", "content": [{"type": "paragraph"}]}


class ScenarioContent(Contract):
    schema_version: Literal[1] = 1
    title: Name
    document: dict[str, JsonValue] = Field(default_factory=empty_document)
    objectives: list[Objective] = Field(default_factory=list, max_length=500)
    nodes: list[FlowNode] = Field(default_factory=list, max_length=5000)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=10000)
    asset_ids: list[UUID] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def validate_identifiers(self) -> Self:
        for ids in (
            [item.id for item in self.nodes],
            [item.id for item in self.edges],
            [item.id for item in self.objectives],
        ):
            if len(ids) != len(set(ids)):
                raise ValueError("Object identifiers must be unique")
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("Asset references must be unique")
        validate_document(self.document, set(self.asset_ids), {node.id for node in self.nodes})
        return self

    def publication_errors(self) -> list[str]:
        ids = {node.id for node in self.nodes}
        return [
            f"Edge {edge.id} references a missing node"
            for edge in self.edges
            if edge.source not in ids or edge.target not in ids
        ]


def validate_document(document: dict[str, JsonValue], assets: set[UUID], nodes: set[UUID]) -> None:
    allowed = {
        "doc",
        "paragraph",
        "text",
        "heading",
        "bulletList",
        "orderedList",
        "listItem",
        "blockquote",
        "codeBlock",
        "hardBreak",
        "horizontalRule",
        "reference",
    }

    def walk(item: JsonValue, depth: int) -> None:
        if depth > 30 or not isinstance(item, dict) or item.get("type") not in allowed:
            raise ValueError("Unsupported document structure")
        if item.get("type") == "reference":
            attrs = item.get("attrs")
            if not isinstance(attrs, dict):
                raise ValueError("Reference attributes are required")
            kind, target = attrs.get("kind"), attrs.get("targetId")
            if kind == "flow":
                if target != "scenario":
                    raise ValueError("Flow references must target this scenario")
            elif kind in {"asset", "node"} and isinstance(target, str):
                if UUID(target) not in (assets if kind == "asset" else nodes):
                    raise ValueError("Document reference does not exist in the scenario")
            else:
                raise ValueError("Unknown document reference")
        marks = item.get("marks", [])
        if not isinstance(marks, list):
            raise ValueError("Invalid document marks")
        for mark in marks:
            if not isinstance(mark, dict) or mark.get("type") not in {
                "bold",
                "italic",
                "strike",
                "code",
                "link",
                "underline",
            }:
                raise ValueError("Unsupported document mark")
            if mark.get("type") == "link":
                attrs = mark.get("attrs")
                href = attrs.get("href") if isinstance(attrs, dict) else None
                if not isinstance(href, str) or not re.match(r"^(https://|mailto:)", href):
                    raise ValueError("Only HTTPS and mailto links are permitted")
        children = item.get("content", [])
        if not isinstance(children, list):
            raise ValueError("Invalid document content")
        for child in children:
            walk(child, depth + 1)

    if document.get("type") != "doc":
        raise ValueError("Document root must be doc")
    walk(document, 0)


class ScenarioView(Contract):
    id: str
    workspace_id: str
    version: int
    content: ScenarioContent
    updated_at: str


class WorkspaceView(Contract):
    id: str
    name: str
    role: Role


class CommentInput(Contract):
    body: str = Field(min_length=1, max_length=5000)
    base_version: int = Field(ge=1)


class PlanningInput(Contract):
    prompt: str = Field(min_length=1, max_length=8000)
    base_version: int = Field(ge=1)
    request_id: UUID


class ProposalContent(Contract):
    summary: str = Field(min_length=1, max_length=2000)
    content: ScenarioContent


class ConnectionInput(Named):
    kind: Literal["sql", "rest", "graph", "mcp"]
    environment_id: UUID
    scope: Literal["workspace", "organization", "assigned"] = "workspace"
    workspace_ids: list[UUID] = Field(default_factory=list, max_length=5000)
    description: str = Field(default="", max_length=2000)


class MembershipInput(Contract):
    object_id: UUID
    role: Role


def mermaid(content: ScenarioContent) -> str:
    def label(value: str) -> str:
        return "".join(c if c.isalnum() or c in " .,:?!_-/" else f"#{ord(c)};" for c in value)

    lines = ["flowchart TD"]
    for node in content.nodes:
        lines.append(f'  n{node.id.hex}["{label(node.label)}"]')
    for edge in content.edges:
        text = f'|"{label(edge.label)}"|' if edge.label else ""
        lines.append(f"  n{edge.source.hex} -->{text} n{edge.target.hex}")
    return "\n".join(lines)
