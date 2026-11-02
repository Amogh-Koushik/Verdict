import hashlib
import io
import zipfile
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.forms import csrf_token_from, log_in_as_admin
from verdict.core.config import Settings
from verdict.core.models import Problem, ProblemTest

PROBLEM_FIELDS = {
    "slug": "sum-of-two",
    "title": "Sum of two numbers",
    "statement_md": "Print a + b.\r\n",
    "time_limit_ms": "1500",
    "memory_limit_mb": "128",
    "is_public": "on",
}


@pytest.fixture
def admin(client: TestClient, db: Session) -> TestClient:
    log_in_as_admin(client, db)
    return client


def submit_problem(client: TestClient, path: str, **changes: str) -> httpx2.Response:
    csrf_token = csrf_token_from(client.get(path))
    fields = {**PROBLEM_FIELDS, **changes, "csrf_token": csrf_token}
    return client.post(path, data=fields, follow_redirects=False)


def zip_of_tests(cases: dict[int, tuple[bytes, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for idx, (given, expected) in cases.items():
            archive.writestr(f"{idx}.in", given)
            archive.writestr(f"{idx}.out", expected)
    return buffer.getvalue()


def upload(client: TestClient, problem_id: int, data: bytes | None) -> httpx2.Response:
    path = f"/admin/problems/{problem_id}/tests"
    csrf_token = csrf_token_from(client.get(path))
    files = {"archive": ("tests.zip", data, "application/zip")} if data is not None else None
    return client.post(path, data={"csrf_token": csrf_token}, files=files, follow_redirects=False)


def create_problem(client: TestClient, db: Session) -> Problem:
    submit_problem(client, "/admin/problems/new")
    return db.scalars(select(Problem)).one()


def test_admin_creates_a_problem_and_is_sent_to_its_tests(admin: TestClient, db: Session) -> None:
    response = submit_problem(admin, "/admin/problems/new")
    problem = db.scalars(select(Problem)).one()
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/problems/{problem.id}/tests"
    assert (problem.slug, problem.title) == ("sum-of-two", "Sum of two numbers")
    assert (problem.time_limit_ms, problem.memory_limit_mb) == (1500, 128)
    assert problem.is_public is True
    assert problem.statement_md == "Print a + b.\n"
    assert problem.tests_version == 0


def test_unchecked_box_makes_the_problem_hidden(admin: TestClient, db: Session) -> None:
    csrf_token = csrf_token_from(admin.get("/admin/problems/new"))
    fields = {key: value for key, value in PROBLEM_FIELDS.items() if key != "is_public"}
    admin.post("/admin/problems/new", data={**fields, "csrf_token": csrf_token})
    assert db.scalars(select(Problem.is_public)).one() is False


def test_invalid_problem_is_shown_again_with_the_reason(admin: TestClient, db: Session) -> None:
    response = submit_problem(admin, "/admin/problems/new", slug="Not A Slug", title="Kept")
    assert response.status_code == 400
    assert "lowercase letters and digits" in response.text
    assert 'value="Kept"' in response.text
    assert db.scalars(select(Problem)).first() is None


def test_slug_already_in_use_is_rejected(admin: TestClient, db: Session) -> None:
    submit_problem(admin, "/admin/problems/new")
    response = submit_problem(admin, "/admin/problems/new", title="Another")
    assert response.status_code == 409
    assert "already uses the slug" in response.text
    assert len(db.scalars(select(Problem)).all()) == 1


def test_admin_edits_a_problem(admin: TestClient, db: Session) -> None:
    problem = create_problem(admin, db)
    response = submit_problem(
        admin, f"/admin/problems/{problem.id}/edit", title="Renamed", time_limit_ms="2000"
    )
    assert response.status_code == 303
    db.refresh(problem)
    assert (problem.title, problem.time_limit_ms) == ("Renamed", 2000)


def test_editing_a_missing_problem_is_not_found(admin: TestClient) -> None:
    assert admin.get("/admin/problems/999/edit").status_code == 404


def test_uploaded_tests_become_version_one(
    admin: TestClient, db: Session, app_settings: Settings
) -> None:
    problem = create_problem(admin, db)
    response = upload(
        admin, problem.id, zip_of_tests({1: (b"1 2\n", b"3\n"), 2: (b"5 5\n", b"10\n")})
    )
    assert response.status_code == 303
    db.refresh(problem)
    assert problem.tests_version == 1
    version_dir = app_settings.problems_dir / str(problem.id) / "v1"
    assert sorted(path.name for path in version_dir.iterdir()) == ["1.in", "1.out", "2.in", "2.out"]
    assert (version_dir / "2.out").read_bytes() == b"10\n"
    rows = db.scalars(select(ProblemTest).order_by(ProblemTest.idx)).all()
    assert [(row.version, row.idx) for row in rows] == [(1, 1), (1, 2)]
    assert rows[0].input_sha256 == hashlib.sha256(b"1 2\n").hexdigest()
    assert "Version 1 is in use, with 2 tests." in admin.get(response.headers["location"]).text


def test_new_upload_adds_a_version_and_keeps_the_old_one(
    admin: TestClient, db: Session, app_settings: Settings
) -> None:
    problem = create_problem(admin, db)
    upload(admin, problem.id, zip_of_tests({1: (b"1 2\n", b"3\n")}))
    upload(admin, problem.id, zip_of_tests({1: (b"1 2\n", b"3\n"), 2: (b"0 0\n", b"0\n")}))
    db.refresh(problem)
    assert problem.tests_version == 2
    problem_dir = app_settings.problems_dir / str(problem.id)
    assert sorted(path.name for path in problem_dir.iterdir()) == ["v1", "v2"]
    assert len(db.scalars(select(ProblemTest).where(ProblemTest.version == 1)).all()) == 1
    assert len(db.scalars(select(ProblemTest).where(ProblemTest.version == 2)).all()) == 2


def test_rejected_upload_changes_nothing(
    admin: TestClient, db: Session, app_settings: Settings
) -> None:
    problem = create_problem(admin, db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("1.in", b"1 2\n")
    response = upload(admin, problem.id, buffer.getvalue())
    assert response.status_code == 400
    assert "Tests 1 have an .in file without its .out" in response.text
    db.refresh(problem)
    assert problem.tests_version == 0
    assert db.scalars(select(ProblemTest)).first() is None
    problem_dir = app_settings.problems_dir / str(problem.id)
    assert list(problem_dir.iterdir()) == [], "the staging folder must be cleaned up"


def test_upload_without_a_file_asks_for_one(admin: TestClient, db: Session) -> None:
    problem = create_problem(admin, db)
    response = upload(admin, problem.id, None)
    assert response.status_code == 400
    assert "Choose a zip file to upload." in response.text


def test_problems_folder_is_created_on_first_upload(
    admin: TestClient, db: Session, app_settings: Settings
) -> None:
    assert not Path(app_settings.problems_dir).exists()
    problem = create_problem(admin, db)
    upload(admin, problem.id, zip_of_tests({1: (b"1\n", b"1\n")}))
    assert (app_settings.problems_dir / str(problem.id) / "v1" / "1.in").exists()
