"""Phase B: HTTP-level authorization (SYSTEM_ARCHITECTURE §3).

Phase A (`test_auth.py` §12–13) proves the *unit* policy — `has_permission`,
`can_see_prospect`, `scope_criterion`. This file drives the real routes with a
signed-in session per role and asserts what a user can actually observe:

* the permission matrix (HTML pages + JSON API), gate on and gate off;
* `owner_id` stamping on create;
* list / facet / dashboard / outreach scoping;
* the row-visibility guard's byte-identical 404 (no existence oracle);
* the duplicate backstop's leak-free message (same for visible + hidden);
* the Account and Users surfaces, including the self-lockout guards.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.models import OutreachMessage, Prospect, User
from app.services import authz
from app.services import dashboard as dashboard_service
from app.services import prospect as prospect_service
from app.services import users as users_service
from tests.conftest import (
    OWNER_PASS,
    OWNER_USER,
    _client,
    _login,
    _seed_owner,
    post_form,
    post_json,
)

#: Password for every seeded role account (policy: >=8 chars, != username).
PASSWORD = "member-pass-9"

#: HTML assertions need an explicit Accept: TestClient's default `*/*` makes
#: main.py's HTTPException handler answer JSON even on page routes.
HTML = {"Accept": "text/html"}

#: Pages behind the `settings` permission.
SETTINGS_PAGES = (
    "/settings/general",
    "/settings/profile",
    "/settings/providers",
    "/settings/import",
    "/settings/usage",
    "/settings/retention",
    "/merge/duplicates",
)


# ===== helpers ============================================================


def _seed_role(db: Session, username: str, role: str, password: str = PASSWORD) -> User:
    """Create ``username`` if absent and return the row."""
    user = users_service.get_user_by_username(db, username)
    if user is None:
        users_service.create_user(db, username=username, password=password, role=role)
        db.commit()
        user = users_service.get_user_by_username(db, username)
    assert user is not None, username
    return user


def _signed_in(db: Session, *, username: str, password: str = PASSWORD) -> TestClient:
    """A fresh client holding a live session for ``username``."""
    client = _client()
    response = _login(client, username=username, password=password)
    assert response.status_code in (302, 303), response.text
    return client


def _identity(db: Session, username: str, role: str) -> SimpleNamespace:
    """Service-layer identity (what `current_user` hands to a route)."""
    user = users_service.get_user_by_username(db, username)
    assert user is not None, username
    return SimpleNamespace(id=user.id, username=user.username, role=role)


def _form(client: TestClient, url: str, data: dict | None = None) -> Response:
    """Seed the CSRF cookie with any GET, then submit a form POST."""
    client.get("/")
    return post_form(client, url, data)


def _api(client: TestClient, url: str, data: dict | None = None) -> Response:
    """Seed the CSRF cookie, then POST JSON (X-CSRF-Token header)."""
    client.get("/")
    return post_json(client, url, data)


def _add_prospect(
    db: Session,
    name: str,
    *,
    owner_id: int | None = None,
    stage: str = "new",
    category: str | None = None,
) -> Prospect:
    prospect = Prospect(business_name=name, stage=stage, category=category, owner_id=owner_id)
    db.add(prospect)
    db.flush()
    return prospect


def _add_message(db: Session, prospect_id: int, text: str) -> OutreachMessage:
    message = OutreachMessage(prospect_id=prospect_id, status="pending_review", current_text=text)
    db.add(message)
    db.flush()
    return message


def _row(db: Session, name: str) -> Prospect:
    prospect = db.query(Prospect).filter_by(business_name=name).one()
    return prospect


# ===== fixtures ===========================================================


@pytest.fixture
def roles(db: Session, token: str) -> dict[str, TestClient]:
    """One signed-in client per role, with the gate switched on."""
    _seed_owner(db)
    _seed_role(db, "site-admin", "admin")
    _seed_role(db, "worker", "member")
    return {
        "owner": _signed_in(db, username=OWNER_USER, password=OWNER_PASS),
        "admin": _signed_in(db, username="site-admin"),
        "member": _signed_in(db, username="worker"),
    }


@pytest.fixture
def other_member(db: Session) -> User:
    """A second member nobody signs in as — the foreign ``owner_id``."""
    return _seed_role(db, "worker2", "member")


@pytest.fixture
def pipeline(db: Session, roles: dict[str, TestClient], other_member: User) -> dict[str, Prospect]:
    """One row in each visibility bucket: shared / mine / theirs."""
    worker = users_service.get_user_by_username(db, "worker")
    assert worker is not None
    rows = {
        "shared": _add_prospect(db, "Shared Pipeline Ltd", stage="new", category="plumbing"),
        "mine": _add_prospect(
            db, "My Pipeline Ltd", stage="replied", category="roofing", owner_id=worker.id
        ),
        "theirs": _add_prospect(
            db, "Their Pipeline Ltd", stage="new", category="electrical", owner_id=other_member.id
        ),
    }
    db.commit()
    return rows


# ===== 1. permission matrix ===============================================


def test_settings_pages_permission_matrix(roles: dict[str, TestClient]) -> None:
    for path in SETTINGS_PAGES:
        denied = roles["member"].get(path, headers=HTML)
        assert denied.status_code == 403, path
        assert "Not allowed" in denied.text, path
        assert "Settings changes require an admin or owner account." in denied.text, path
        for role in ("admin", "owner"):
            allowed = roles[role].get(path, headers=HTML)
            assert allowed.status_code not in (401, 403), (role, path, allowed.status_code)
    assert roles["admin"].get("/settings/general", headers=HTML).status_code == 200


def test_danger_surfaces_permission_matrix(
    roles: dict[str, TestClient], pipeline: dict[str, Prospect]
) -> None:
    target = pipeline["shared"].id

    denied = _form(roles["member"], f"/retention/{target}/clear")
    assert denied.status_code == 403
    assert "This action requires an admin or owner account." in denied.text

    for role in ("admin", "owner"):
        allowed = _form(roles[role], f"/retention/{target}/clear")
        # Not flagged -> the handler 404s; the point is only that the role passes.
        assert allowed.status_code not in (401, 403), role


def test_users_page_permission_matrix(roles: dict[str, TestClient]) -> None:
    for role in ("member", "admin"):
        denied = roles[role].get("/settings/users", headers=HTML)
        assert denied.status_code == 403, role
        assert "Only the owner can manage users." in denied.text, role
    assert roles["owner"].get("/settings/users", headers=HTML).status_code == 200


def test_api_permission_matrix(roles: dict[str, TestClient]) -> None:
    denied = roles["member"].get("/api/v1/settings/profile")
    assert denied.status_code == 403
    assert denied.json() == {"detail": "Settings changes require an admin or owner account."}

    denied = _api(roles["member"], "/api/v1/retention/sweep", {"months": 12})
    assert denied.status_code == 403
    assert denied.json() == {"detail": "This action requires an admin or owner account."}

    assert roles["admin"].get("/api/v1/settings/profile").status_code == 200
    for role in ("admin", "owner"):
        allowed = _api(roles[role], "/api/v1/retention/sweep", {"months": 12})
        assert allowed.status_code != 403, role


def test_gate_off_passes_settings_through_and_hides_identity_surfaces(
    db: Session, no_token: None
) -> None:
    """Gate unset -> the permission deps are inert, identity surfaces are not."""
    client = _client()
    assert client.get("/settings/general", headers=HTML).status_code == 200
    assert client.get("/api/v1/settings/profile").status_code == 200

    users_page = client.get("/settings/users", headers=HTML)
    assert users_page.status_code == 303
    assert users_page.headers["location"] == "/"
    account = client.get("/settings/account", headers=HTML)
    assert account.status_code == 303
    assert account.headers["location"] == "/"

    # ...and the identity tabs vanish from the settings strip entirely.
    views = client.get("/settings/saved-views", headers=HTML)
    assert views.status_code == 200
    assert "/settings/account" not in views.text
    assert "/settings/users" not in views.text


# ===== 2. owner_id stamping ===============================================


def test_member_create_stamps_owner_id(db: Session, roles: dict[str, TestClient]) -> None:
    response = _api(roles["member"], "/api/v1/prospects", {"business_name": "Member Made Ltd"})
    assert response.status_code == 201, response.text
    worker = users_service.get_user_by_username(db, "worker")
    assert worker is not None
    assert _row(db, "Member Made Ltd").owner_id == worker.id


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_unscoped_roles_create_shared_rows(
    db: Session, roles: dict[str, TestClient], role: str
) -> None:
    name = f"{role} made Ltd"
    response = _api(roles[role], "/api/v1/prospects", {"business_name": name})
    assert response.status_code == 201, response.text
    assert _row(db, name).owner_id is None  # NULL = shared, the team's pipeline


def test_gate_off_create_is_shared(db: Session, no_token: None) -> None:
    client = _client()
    response = _api(client, "/api/v1/prospects", {"business_name": "Loopback Ltd"})
    assert response.status_code == 201, response.text
    assert _row(db, "Loopback Ltd").owner_id is None


def test_html_create_stamps_owner_id(db: Session, roles: dict[str, TestClient]) -> None:
    response = _form(roles["member"], "/prospects/new", {"business_name": "Form Made Ltd"})
    assert response.status_code == 303, response.text
    worker = users_service.get_user_by_username(db, "worker")
    assert worker is not None
    assert _row(db, "Form Made Ltd").owner_id == worker.id


# ===== 3. list / facet scoping ============================================


def test_member_board_excludes_foreign_rows(
    roles: dict[str, TestClient], pipeline: dict[str, Prospect]
) -> None:
    member = roles["member"].get("/", headers=HTML)
    assert member.status_code == 200
    assert "Shared Pipeline Ltd" in member.text
    assert "My Pipeline Ltd" in member.text
    assert "Their Pipeline Ltd" not in member.text

    owner = roles["owner"].get("/", headers=HTML)
    assert "Their Pipeline Ltd" in owner.text


def test_api_list_is_scoped_per_role(
    roles: dict[str, TestClient], pipeline: dict[str, Prospect]
) -> None:
    def names(client: TestClient) -> set[str]:
        response = client.get("/api/v1/prospects")
        assert response.status_code == 200, response.text
        return {row["business_name"] for row in response.json()}

    visible = {"Shared Pipeline Ltd", "My Pipeline Ltd"}
    assert names(roles["member"]) == visible
    assert names(roles["admin"]) == visible | {"Their Pipeline Ltd"}
    assert names(roles["owner"]) == visible | {"Their Pipeline Ltd"}


def test_filters_facets_are_scoped(
    roles: dict[str, TestClient], pipeline: dict[str, Prospect]
) -> None:
    member = roles["member"].get("/prospects/filters", headers=HTML)
    assert member.status_code == 200
    assert "plumbing" in member.text
    assert "roofing" in member.text
    assert "electrical" not in member.text  # a facet must not leak a hidden row

    owner = roles["owner"].get("/prospects/filters", headers=HTML)
    assert "electrical" in owner.text


# ===== 4. row-visibility guard ============================================


def test_hidden_prospect_404_is_byte_identical_to_missing(
    roles: dict[str, TestClient], pipeline: dict[str, Prospect]
) -> None:
    member = roles["member"]
    hidden = member.get(f"/prospects/{pipeline['theirs'].id}", headers=HTML)
    missing = member.get("/prospects/999999", headers=HTML)

    assert hidden.status_code == 404
    assert missing.status_code == 404
    assert hidden.content == missing.content  # byte-identical -> no oracle
    assert hidden.text == "Prospect not found"

    # owner/admin see every row
    url = f"/prospects/{pipeline['theirs'].id}"
    assert roles["owner"].get(url, headers=HTML).status_code == 200
    assert roles["admin"].get(url, headers=HTML).status_code == 200


def test_hidden_rows_404_over_json_and_on_child_paths(
    db: Session, roles: dict[str, TestClient], pipeline: dict[str, Prospect]
) -> None:
    member = roles["member"]
    theirs = pipeline["theirs"].id

    hidden = member.get(f"/api/v1/prospects/{theirs}")
    missing = member.get("/api/v1/prospects/999999")
    assert hidden.status_code == 404
    assert missing.status_code == 404
    assert hidden.json() == missing.json() == {"detail": "Prospect not found"}

    # A child row of a hidden prospect answers exactly like a missing child.
    message = _add_message(db, theirs, "hidden draft")
    db.commit()
    child = member.get(f"/messages/{message.id}/edit", headers=HTML)
    child_missing = member.get("/messages/999999/edit", headers=HTML)
    assert child.status_code == 404
    assert child_missing.status_code == 404
    assert child.content == child_missing.content
    assert child.text == "Message not found"


# ===== 5. dashboard + outreach scoping ====================================


def test_dashboard_counts_are_scoped(db: Session, pipeline: dict[str, Prospect]) -> None:
    member = _identity(db, "worker", "member")

    assert dashboard_service.total_prospects(db) == 3
    assert dashboard_service.total_prospects(db, visible_to=member) == 2

    unrestricted = dashboard_service.stage_counts(db)
    scoped = dashboard_service.stage_counts(db, visible_to=member)
    assert unrestricted["new"] == 2
    assert scoped["new"] == 1  # only the shared row; theirs is hidden
    assert scoped["replied"] == 1


def test_needs_attention_is_scoped(db: Session, pipeline: dict[str, Prospect]) -> None:
    _add_message(db, pipeline["mine"].id, "mine draft")
    _add_message(db, pipeline["theirs"].id, "theirs draft")
    db.commit()

    def pending(items: list[dict[str, Any]]) -> int:
        return sum(int(i["count"]) for i in items if i["kind"] == "pending_review")

    member = _identity(db, "worker", "member")
    assert pending(dashboard_service.needs_attention(db, visible_to=member)) == 1
    assert pending(dashboard_service.needs_attention(db)) == 2


def test_outreach_region_is_scoped(
    db: Session, roles: dict[str, TestClient], pipeline: dict[str, Prospect]
) -> None:
    _add_message(db, pipeline["mine"].id, "mine visible draft")
    _add_message(db, pipeline["theirs"].id, "theirs hidden draft")
    db.commit()

    member = roles["member"].get("/", headers=HTML)
    assert member.status_code == 200
    assert "mine visible draft" in member.text
    assert "theirs hidden draft" not in member.text
    assert "Their Pipeline Ltd" not in member.text
    assert "1 in flight" in member.text

    owner = roles["owner"].get("/", headers=HTML)
    assert "theirs hidden draft" in owner.text
    assert "2 in flight" in owner.text


# ===== 6. dedupe scoping + leak-free backstop =============================


def test_dupe_check_is_scoped(db: Session, pipeline: dict[str, Prospect]) -> None:
    theirs = pipeline["theirs"]
    theirs.normalized_name = "their pipeline ltd"
    db.commit()

    member_hit = prospect_service.check_duplicates(
        db, business_name="Their Pipeline Ltd", visible_to=_identity(db, "worker", "member")
    )
    owner_hit = prospect_service.check_duplicates(
        db, business_name="Their Pipeline Ltd", visible_to=_identity(db, OWNER_USER, "owner")
    )

    assert member_hit["exact"] is None
    assert member_hit["exact_field"] is None
    assert owner_hit["exact"] is theirs
    assert owner_hit["exact_field"] == "normalized_name"


def test_duplicate_message_is_identical_for_visible_and_hidden_rows(
    db: Session, roles: dict[str, TestClient], other_member: User
) -> None:
    """A member must not be able to tell a visible conflict from a hidden one.

    The scoped pre-check sees shared/own rows; the unique-index backstop sees
    everything. If the two produced different copy, "you got the generic
    message" would mean "the conflicting row exists but is hidden from you".
    """
    visible = _add_prospect(db, "Acme Visible Ltd")
    visible.normalized_name = "acme visible ltd"
    hidden = _add_prospect(db, "Acme Hidden Ltd", owner_id=other_member.id)
    hidden.normalized_name = "acme hidden ltd"
    db.commit()

    member = roles["member"]
    visible_hit = _api(member, "/api/v1/prospects", {"business_name": "Acme Visible Ltd"})
    hidden_hit = _api(member, "/api/v1/prospects", {"business_name": "Acme Hidden Ltd"})

    assert visible_hit.status_code == 409, visible_hit.text
    assert hidden_hit.status_code == 409, hidden_hit.text
    assert visible_hit.json() == hidden_hit.json()  # identical -> no oracle

    detail = hidden_hit.json()["detail"]
    assert detail == "A prospect with this business name already exists."
    assert "Acme" not in detail
    assert str(hidden.id) not in detail

    db.expire_all()
    assert db.query(Prospect).filter_by(business_name="Acme Hidden Ltd").count() == 1

    # An owner sees every row, so naming it stays allowed (and useful).
    owner_hit = _api(roles["owner"], "/api/v1/prospects", {"business_name": "Acme Visible Ltd"})
    assert owner_hit.status_code == 409
    assert owner_hit.json()["detail"].startswith(
        "Duplicate prospect already exists: Acme Visible Ltd"
    )


def test_update_path_duplicate_message_is_leak_free(
    db: Session, other_member: User, pipeline: dict[str, Prospect]
) -> None:
    hidden = _add_prospect(db, "Taken Name Ltd", owner_id=other_member.id)
    hidden.normalized_name = "taken name ltd"
    mine = pipeline["mine"]
    mine.normalized_name = "my pipeline ltd"
    db.commit()

    _, member_error = prospect_service.update_prospect(
        db, mine.id, visible_to=_identity(db, "worker", "member"), business_name="Taken Name Ltd"
    )
    assert member_error == "A prospect with this business name already exists."

    # The same edit as an owner (who can see the row) still names it.
    _, owner_error = prospect_service.update_prospect(
        db, mine.id, visible_to=_identity(db, OWNER_USER, "owner"), business_name="Taken Name Ltd"
    )
    assert owner_error is not None
    assert owner_error.startswith("Would duplicate existing prospect: Taken Name Ltd")


# ===== 7. Account surface ================================================


def test_account_page_renders_for_every_role(roles: dict[str, TestClient]) -> None:
    for role, client in roles.items():
        page = client.get("/settings/account", headers=HTML)
        assert page.status_code == 200, role
        assert "Change password" in page.text, role
        assert "Active sessions" in page.text, role


def test_change_password_revokes_other_sessions(db: Session, token: str) -> None:
    _seed_owner(db)
    first = _signed_in(db, username=OWNER_USER, password=OWNER_PASS)
    second = _signed_in(db, username=OWNER_USER, password=OWNER_PASS)

    changed = _form(
        first,
        "/settings/account/password",
        {
            "current_password": OWNER_PASS,
            "new_password": "rotated-pass-9",
            "confirm": "rotated-pass-9",
        },
    )
    assert changed.status_code == 303, changed.text

    # This device stays signed in...
    assert first.get("/", headers=HTML).status_code == 200
    # ...every other session dies server-side.
    bounced = second.get("/", headers=HTML)
    assert bounced.status_code == 302
    assert bounced.headers["location"].startswith("/login")

    fresh = _client()
    assert _login(fresh, username=OWNER_USER, password="rotated-pass-9").status_code == 303
    stale = _client()
    assert _login(stale, username=OWNER_USER, password=OWNER_PASS).status_code == 401


def test_change_password_is_rate_limited(db: Session, token: str) -> None:
    """A hijacked session must not brute-force the *current* password."""
    _seed_owner(db)
    client = _signed_in(db, username=OWNER_USER, password=OWNER_PASS)
    client.get("/settings/account")

    payload = {
        "current_password": "not-the-password",
        "new_password": "whatever-1234",
        "confirm": "whatever-1234",
    }
    for attempt in range(5):
        response = post_form(client, "/settings/account/password", payload)
        assert response.status_code == 400, attempt

    blocked = post_form(client, "/settings/account/password", payload)
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text


# ===== 8. Users surface ==================================================


def _user(db: Session, username: str) -> User:
    row = users_service.get_user_by_username(db, username)
    assert row is not None, username
    return row


def test_users_page_crud_flow(db: Session, roles: dict[str, TestClient]) -> None:
    owner = roles["owner"]
    page = owner.get("/settings/users", headers=HTML)
    assert page.status_code == 200
    assert "Add user" in page.text

    created = _form(
        owner,
        "/settings/users",
        {
            "username": "newbie",
            "password": "newbie-pass-1",
            "confirm": "newbie-pass-1",
            "role": "member",
        },
    )
    assert created.status_code == 303, created.text
    assert _user(db, "newbie").role == "member"

    promoted = _form(owner, f"/settings/users/{_user(db, 'newbie').id}/role", {"role": "admin"})
    assert promoted.status_code == 303
    db.expire_all()
    assert _user(db, "newbie").role == "admin"

    disabled = _form(owner, f"/settings/users/{_user(db, 'newbie').id}/toggle", {})
    assert disabled.status_code == 303
    db.expire_all()
    assert _user(db, "newbie").disabled is True

    re_enabled = _form(owner, f"/settings/users/{_user(db, 'newbie').id}/toggle", {})
    assert re_enabled.status_code == 303
    db.expire_all()
    assert _user(db, "newbie").disabled is False

    # Deleting a user hands their private rows to the shared pool, never orphans them.
    owned = _add_prospect(db, "Newbie Owned Ltd", owner_id=_user(db, "newbie").id)
    db.commit()
    deleted = _form(owner, f"/settings/users/{_user(db, 'newbie').id}/delete", {})
    assert deleted.status_code == 303
    db.expire_all()
    assert db.get(User, owned.id) is not None
    assert db.get(Prospect, owned.id).owner_id is None
    assert db.query(User).filter_by(username="newbie").first() is None


def test_self_lockout_guards(db: Session, roles: dict[str, TestClient]) -> None:
    owner = roles["owner"]
    owner_id = _user(db, OWNER_USER).id

    refused = _form(owner, f"/settings/users/{owner_id}/toggle", {})
    assert refused.status_code == 303
    assert "You cannot disable your own account." in unquote(refused.headers["location"])
    db.expire_all()
    assert _user(db, OWNER_USER).disabled is False

    refused = _form(owner, f"/settings/users/{owner_id}/delete", {})
    assert refused.status_code == 303
    assert "You cannot delete your own account." in unquote(refused.headers["location"])
    assert db.get(User, owner_id) is not None

    refused = _form(owner, f"/settings/users/{owner_id}/role", {"role": "member"})
    assert refused.status_code == 303
    assert "last active owner" in unquote(refused.headers["location"])
    db.expire_all()
    assert _user(db, OWNER_USER).role == "owner"

    # ...until a second active owner exists.
    created = _form(
        owner,
        "/settings/users",
        {
            "username": "boss2",
            "password": "boss2-pass-1",
            "confirm": "boss2-pass-1",
            "role": "owner",
        },
    )
    assert created.status_code == 303, created.text
    allowed = _form(owner, f"/settings/users/{owner_id}/role", {"role": "admin"})
    assert allowed.status_code == 303
    db.expire_all()
    assert _user(db, OWNER_USER).role == "admin"


# ===== 9. navigation gating ===============================================


def test_settings_tabs_are_filtered_by_role(roles: dict[str, TestClient]) -> None:
    member = roles["member"].get("/settings/saved-views", headers=HTML)
    assert member.status_code == 200
    assert "/settings/account" in member.text  # self-service, every role
    assert "/settings/saved-views" in member.text
    assert "/settings/users" not in member.text  # owner-only
    assert "/settings/general" not in member.text  # settings permission

    owner = roles["owner"].get("/settings/saved-views", headers=HTML)
    assert owner.status_code == 200
    assert "/settings/users" in owner.text
    assert "/settings/general" in owner.text


def test_member_sees_no_settings_cluster_cards(roles: dict[str, TestClient]) -> None:
    member = roles["member"].get("/", headers=HTML)
    assert member.status_code == 200
    assert 'data-setup-card="status"' in member.text
    assert 'data-setup-card="saved_views"' in member.text
    assert 'data-setup-card="profile"' not in member.text
    assert 'href="/settings/providers"' not in member.text
    # The Account rail link is role-agnostic (self-service) but gate-bound.
    assert 'href="/settings/account"' in member.text

    owner = roles["owner"].get("/", headers=HTML)
    assert 'data-setup-card="profile"' in owner.text
    assert 'href="/settings/providers"' in owner.text
    assert 'href="/settings/account"' in owner.text


def test_identity_nav_disappears_when_the_gate_is_off(db: Session, no_token: None) -> None:
    response = _client().get("/", headers=HTML)
    assert response.status_code == 200
    assert 'href="/settings/account"' not in response.text
    # Gate off is pass-through: the settings cluster stays fully visible.
    assert 'data-setup-card="profile"' in response.text


# ===== 10. policy surface used by the routes ==============================


def test_stamp_owner_is_role_scoped() -> None:
    member = SimpleNamespace(id=7, username="worker", role="member")
    admin = SimpleNamespace(id=8, username="boss", role="admin")
    assert authz.stamp_owner(member) == 7
    assert authz.stamp_owner(admin) is None
    assert authz.stamp_owner(None) is None
