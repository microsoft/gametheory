import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from gametheory.assets import validate_upload
from gametheory.config import Settings
from gametheory.domain import ProposalContent, ScenarioContent, mermaid
from gametheory.service import expected_version


def test_defaults_and_immutable_serialization():
    first = ScenarioContent(title="Regional flood")
    frozen = first.model_dump_json()
    first.title = "Revised flood"
    assert ScenarioContent.model_validate_json(frozen).title == "Regional flood"
    assert ScenarioContent(title="Other").nodes == []


def test_graph_and_mermaid_escape_untrusted_labels():
    a, b, edge = (str(uuid.uuid4()) for _ in range(3))
    content = ScenarioContent.model_validate(
        {
            "title": "Exercise",
            "nodes": [
                {
                    "id": a,
                    "kind": "action",
                    "label": 'A"] --> evil["',
                    "position": {"x": 0, "y": 0},
                },
                {"id": b, "kind": "condition", "label": "Occupancy", "position": {"x": 1, "y": 1}},
            ],
            "edges": [{"id": edge, "source": a, "target": b, "label": "Above 85%"}],
        }
    )
    assert not content.publication_errors()
    assert 'A"] --> evil["' not in mermaid(content)
    assert "Above 85#37;" in mermaid(content)
    content.nodes.pop()
    assert "missing node" in content.publication_errors()[0]


@pytest.mark.parametrize(
    "document",
    [
        {"type": "script", "text": "alert(1)"},
        {"type": "doc", "content": [{"type": "image", "attrs": {"src": "https://untrusted.test"}}]},
        {
            "type": "doc",
            "content": [
                {"type": "reference", "attrs": {"kind": "asset", "targetId": str(uuid.uuid4())}}
            ],
        },
        {
            "type": "doc",
            "content": [
                {
                    "type": "text",
                    "text": "link",
                    "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}],
                }
            ],
        },
    ],
)
def test_invalid_documents_are_explicitly_rejected(document):
    with pytest.raises(ValidationError):
        ScenarioContent(title="Exercise", document=document)


def test_proposals_cannot_include_authority_changes():
    with pytest.raises(ValidationError):
        ProposalContent.model_validate(
            {
                "summary": "Change",
                "content": {"title": "Exercise"},
                "grant_owner": True,
            }
        )


@pytest.mark.parametrize(
    "value,status", [(None, 428), ("*", 400), ('W/"1"', 400), ('"1","2"', 400), ('"0"', 400)]
)
def test_conditional_write_contract(value, status):
    with pytest.raises(HTTPException) as caught:
        expected_version(value)
    assert caught.value.status_code == status
    assert expected_version('"5"') == 5


def test_cloud_profiles_fail_closed():
    with pytest.raises(ValueError, match="custom cloud"):
        Settings(_env_file=None, AZURE_CLOUD="custom")
    with pytest.raises(ValueError, match="not validated"):
        Settings(_env_file=None, AZURE_CLOUD="government", planning_enabled=True)
    assert Settings(_env_file=None, AZURE_CLOUD="government").profile.authority.endswith(".us")
    with pytest.raises(ValueError, match="local emulator"):
        Settings(
            _env_file=None, scheduler_endpoint="http://remote.example:8080", scheduler_emulator=True
        )
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(_env_file=None, scheduler_endpoint="http://localhost:8080")


def test_assets_reject_active_types_and_mismatched_bytes():
    for data, media in [
        (b"<svg/>", "image/svg+xml"),
        (b"not png", "image/png"),
        (b"\x00", "text/plain"),
        (b"{bad}", "application/json"),
    ]:
        with pytest.raises(HTTPException):
            validate_upload(data, media)
    assert len(validate_upload(b'{"purpose":"test"}', "application/json")) == 64
