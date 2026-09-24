"""Role normalization tests.

The role literal and every role whitelist (identity store, token payload
mapping, the service-layer ``set_role`` guard, and the ``SetRoleRequest``
API validator) must converge on ``normalize_role``/``VALID_ROLES`` as a
single source of truth. Legal roles pass through; anything else degrades
to the default (least-privilege) instead of being silently kept.
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from deeptutor.api.routers.auth import SetRoleRequest
from deeptutor.multi_user.context import user_from_token_payload
from deeptutor.multi_user.models import VALID_ROLES, normalize_role

# ---------------------------------------------------------------- models


def test_valid_roles_contains_exactly_the_documented_roles():
    # K12 fork: five roles (upstream ships two). The whitelist itself is the
    # contract — whatever it holds must match the fork's Role literal.
    assert VALID_ROLES == frozenset({"admin", "teacher", "student", "parent", "user"})


def test_normalize_role_passes_legal_roles_through():
    assert normalize_role("admin") == "admin"
    assert normalize_role("user") == "user"
    # K12 fork: the extra roles are legal, not degraded.
    assert normalize_role("teacher") == "teacher"
    assert normalize_role("student") == "student"
    assert normalize_role("parent") == "parent"


# K12 fork: teacher/student are legal roles here, so they leave the bogus list
# (upstream's two-role model treats them as unknown values).
@pytest.mark.parametrize("bogus", ["", "superadmin", "Admin", "root"])
def test_normalize_role_degrades_unknown_values_to_user(bogus):
    assert normalize_role(bogus) == "user"


def test_normalize_role_honors_custom_default():
    assert normalize_role("nope", default="admin") == "admin"
    assert normalize_role("admin", default="admin") == "admin"


# ------------------------------------------------------- identity store


def test_set_role_accepts_legal_roles_and_readback_preserves_them(seed_user):
    from deeptutor.multi_user.identity import load_users
    from deeptutor.services.auth import set_role

    seed_user("alice", "password1234")
    seed_user("bob", "password1234")
    assert set_role("alice", "admin") is True
    assert set_role("bob", "user") is True
    # Re-read through the canonicalization path: legal roles must survive it.
    roles = {username: record["role"] for username, record in load_users().items()}
    assert roles["alice"] == "admin"
    assert roles["bob"] == "user"


def test_set_role_rejects_unknown_roles(mu_isolated_root):
    from deeptutor.multi_user.identity import load_users
    from deeptutor.services.auth import set_role

    with pytest.raises(ValueError):
        set_role("alice", "superadmin")
    assert load_users() == {}


def test_corrupted_stored_role_degrades_to_user_on_load(mu_isolated_root):
    from deeptutor.multi_user import identity

    identity.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # "bob" is a non-first account, so its canonical default is "user"; the
    # corrupted "superadmin" value must degrade to that, never pass through.
    identity.USERS_FILE.write_text(
        '{"alice": {"id": "u_a", "hash": "h"},'
        ' "bob": {"id": "u_b", "hash": "h", "role": "superadmin"}}',
        encoding="utf-8",
    )
    roles = {username: record["role"] for username, record in identity.load_users().items()}
    assert roles["bob"] == "user"


# ------------------------------------------------- token payload mapping


def test_token_payload_maps_legal_roles_through():
    current = user_from_token_payload(SimpleNamespace(user_id="u_a", username="a", role="admin"))
    assert current.role == "admin"
    assert current.is_admin is True
    current = user_from_token_payload(SimpleNamespace(user_id="u_b", username="b", role="user"))
    assert current.role == "user"
    assert current.is_admin is False


def test_token_payload_degrades_unknown_roles_to_user():
    current = user_from_token_payload(
        SimpleNamespace(user_id="u_x", username="x", role="superadmin")
    )
    assert current.role == "user"
    assert current.is_admin is False


# ------------------------------------------------------- API validator


def test_set_role_request_accepts_legal_roles():
    assert SetRoleRequest(role="admin").role == "admin"
    assert SetRoleRequest(role="user").role == "user"


def test_set_role_request_rejects_unknown_roles():
    with pytest.raises(ValidationError):
        SetRoleRequest(role="superadmin")
