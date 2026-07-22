"""Robustness guards: body-size limit, input length caps, page-size clamps."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import AgentReg, MemoryNew, MsgNew, StreamIn, TaskNew, app

client = TestClient(app)


def test_oversized_body_rejected_with_413():
    """A body over the limit is refused before any route or auth runs."""
    big = b'{"x":"' + b"a" * 300_000 + b'"}'
    r = client.post("/api/tasks", content=big,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_normal_body_not_rejected_by_size():
    """A small body passes the size gate (it then fails auth, not size)."""
    r = client.post("/api/tasks", json={"title": "hi"})
    assert r.status_code != 413        # 401 (no token) is fine here


@pytest.mark.parametrize("model,field,length", [
    (TaskNew, "title", 400),
    (MsgNew, "body", 9000),
    (MemoryNew, "body", 60000),
    (StreamIn, "content", 9000),
    (AgentReg, "name", 200),
])
def test_field_length_caps(model, field, length):
    with pytest.raises(ValidationError):
        model(**{field: "a" * length})


def test_empty_required_fields_rejected():
    with pytest.raises(ValidationError):
        TaskNew(title="")
    with pytest.raises(ValidationError):
        MsgNew(body="")


def test_depends_on_is_bounded():
    with pytest.raises(ValidationError):
        TaskNew(title="ok", depends_on=[str(i) for i in range(200)])
