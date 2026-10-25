from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import jwt
from jwt import PyJWKClientConnectionError
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from helpers import NOW, make_case
from services.warehouse_api.auth import clear_jwk_cache, get_jwk_client
from services.warehouse_api.main import create_app
from services.warehouse_api.repository import SQLiteWarehouseRepository
from shared.config import Settings
from shared.models import AuditEvent, OperatorRole, RecoveryPlanStatus
from tools.recovery_optimizer import plan_recovery

JWT_SECRET = "super-secret-supabase-jwt-key-for-testing-at-least-32-bytes"
FUTURE = datetime.now(timezone.utc) + timedelta(days=30)
PAST = datetime.now(timezone.utc) - timedelta(days=1)

SUPABASE_PROJECT_URL = "https://rklvrimqejqtzoyloawf.supabase.co"
SUPABASE_EXPECTED_ISSUER = "https://rklvrimqejqtzoyloawf.supabase.co/auth/v1"
SUPABASE_EXPECTED_AUDIENCE = "authenticated"


@pytest.fixture
def ec_keys():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()
    nums = pub.public_numbers()

    def b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(32, "big")).decode("ascii").rstrip("=")

    kid = "test-es256-kid-01"
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": b64(nums.x),
        "y": b64(nums.y),
        "kid": kid,
        "alg": "ES256",
        "use": "sig",
    }
    return priv, pub, jwk, kid


def generate_es256_jwt(
    private_key,
    *,
    kid: str | None = "test-es256-kid-01",
    sub: str = "operator-uuid-001",
    email: str = "procurement.lead@example.com",
    roles: list[str] | None = None,
    user_metadata: dict | None = None,
    exp: datetime | None = None,
    aud: str = SUPABASE_EXPECTED_AUDIENCE,
    iss: str = SUPABASE_EXPECTED_ISSUER,
    include_sub: bool = True,
    include_exp: bool = True,
    include_iss: bool = True,
    include_aud: bool = True,
) -> str:
    exp_dt = exp or (datetime.now(timezone.utc) + timedelta(hours=2))
    payload: dict = {
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    if include_sub:
        payload["sub"] = sub
    if email:
        payload["email"] = email
    if include_aud:
        payload["aud"] = aud
    if include_exp:
        payload["exp"] = int(exp_dt.timestamp())
    if include_iss:
        payload["iss"] = iss
    if roles is not None:
        payload["app_metadata"] = {"roles": roles}
    if user_metadata is not None:
        payload["user_metadata"] = user_metadata

    headers = {}
    if kid is not None:
        headers["kid"] = kid

    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


def generate_hs256_jwt(
    secret: str = JWT_SECRET,
    *,
    sub: str = "operator-uuid-001",
    email: str = "procurement.lead@example.com",
    roles: list[str] | None = None,
    user_metadata: dict | None = None,
    exp: datetime | None = None,
    aud: str = "authenticated",
    iss: str | None = None,
) -> str:
    exp_dt = exp or (datetime.now(timezone.utc) + timedelta(hours=2))
    payload: dict = {
        "sub": sub,
        "email": email,
        "aud": aud,
        "exp": int(exp_dt.timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    if roles is not None:
        payload["app_metadata"] = {"roles": roles}
    if user_metadata is not None:
        payload["user_metadata"] = user_metadata
    if iss is not None:
        payload["iss"] = iss
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def test_repo(tmp_path):
    repo = SQLiteWarehouseRepository(tmp_path / "auth_approvals.db")
    repo.initialize()
    yield repo
    repo.close()


@pytest.fixture
def es256_settings():
    return Settings(
        database_backend="sqlite",
        warehouse_api_key=None,
        supabase_url=SUPABASE_PROJECT_URL,
        supabase_jwt_issuer=SUPABASE_EXPECTED_ISSUER,
        supabase_jwt_audience=SUPABASE_EXPECTED_AUDIENCE,
        supabase_jwt_algorithms="ES256",
        supabase_jwt_secret=None,  # strictly asymmetric; no shared secret
    )


@pytest.fixture
def mock_jwks_client(ec_keys, es256_settings):
    clear_jwk_cache()
    priv, pub, jwk, kid = ec_keys
    jwks_url = es256_settings.get_jwks_url()
    client = get_jwk_client(jwks_url)
    client.fetch_data = lambda: {"keys": [jwk]}
    yield client
    clear_jwk_cache()


@pytest.fixture
def client(test_repo, es256_settings, mock_jwks_client):
    app = create_app(settings=es256_settings, repository=test_repo)
    with TestClient(app, base_url="http://testclient") as test_client:
        yield test_client


@pytest.fixture
def setup_case_and_plan(test_repo):
    case = make_case(case_id="CASE-AUTH-001")
    plan = plan_recovery(case, as_of=NOW)
    test_repo.create_case(case)
    test_repo.save_plan(plan)
    return case, plan


# ==============================================================================
# ES256 Asymmetric Verification Tests
# ==============================================================================

def test_es256_valid_token_verification(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, kid = ec_keys
    token = generate_es256_jwt(
        priv,
        kid=kid,
        email="procurement.officer@stockrescue.io",
        roles=["procurement_approver"],
    )

    cost_limit = plan.total_cost + Decimal("150.00")
    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "max_total_cost": str(cost_limit),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["approved_by"] == "procurement.officer@stockrescue.io"
    assert data["plan_fingerprint"] == plan.compute_fingerprint()
    assert data["case_id"] == case.case_id
    assert data["plan_id"] == plan.plan_id


def test_es256_wrong_signature_rejected(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    _, _, _, kid = ec_keys
    # Generate a DIFFERENT EC key pair for signing to simulate forged signature
    attacker_priv = ec.generate_private_key(ec.SECP256R1())
    forged_token = generate_es256_jwt(
        attacker_priv,
        kid=kid,
        roles=["procurement_approver"],
    )

    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {forged_token}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401
    assert "signature is invalid" in res.json()["detail"]


def test_es256_unknown_key_id_rejected(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, _ = ec_keys
    # Token specifies an unknown kid not in JWKS
    token = generate_es256_jwt(
        priv,
        kid="unknown-nonexistent-kid-999",
        roles=["procurement_approver"],
    )

    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401
    assert "matching public signing key was not found" in res.json()["detail"]


def test_es256_missing_kid_rejected(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, _ = ec_keys
    # Token without kid header
    token = generate_es256_jwt(priv, kid=None, roles=["procurement_approver"])

    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401
    assert "missing key id (kid)" in res.json()["detail"]


def test_es256_invalid_issuer_rejected(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, kid = ec_keys
    token = generate_es256_jwt(
        priv,
        kid=kid,
        iss="https://rogue-supabase-project.co/auth/v1",
        roles=["procurement_approver"],
    )

    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401
    assert "issuer is invalid" in res.json()["detail"]


def test_es256_wrong_audience_rejected(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, kid = ec_keys
    token = generate_es256_jwt(
        priv,
        kid=kid,
        aud="unauthenticated_guest",
        roles=["procurement_approver"],
    )

    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401
    assert "audience is invalid" in res.json()["detail"]


def test_es256_expired_token_rejected(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, kid = ec_keys
    token = generate_es256_jwt(
        priv,
        kid=kid,
        exp=PAST,
        roles=["procurement_approver"],
    )

    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401
    assert "expired" in res.json()["detail"]


def test_es256_missing_required_claims_rejected(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, kid = ec_keys

    # Missing 'sub' claim
    token_no_sub = generate_es256_jwt(priv, kid=kid, include_sub=False, roles=["procurement_approver"])
    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token_no_sub}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401

    # Missing 'iss' claim
    token_no_iss = generate_es256_jwt(priv, kid=kid, include_iss=False, roles=["procurement_approver"])
    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token_no_iss}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401

    # Missing 'aud' claim
    token_no_aud = generate_es256_jwt(priv, kid=kid, include_aud=False, roles=["procurement_approver"])
    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {token_no_aud}"},
        json={
            "max_total_cost": str(plan.total_cost + Decimal("100.00")),
            "currency": plan.currency,
            "expires_at": FUTURE.isoformat(),
        },
    )
    assert res.status_code == 401


def test_es256_unavailable_jwks_fails_closed(test_repo, es256_settings, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, kid = ec_keys
    token = generate_es256_jwt(priv, kid=kid, roles=["procurement_approver"])

    clear_jwk_cache()
    client_obj = get_jwk_client(es256_settings.get_jwks_url())

    def unreachable():
        raise PyJWKClientConnectionError("Connection to JWKS timed out")

    client_obj.fetch_data = unreachable

    app = create_app(settings=es256_settings, repository=test_repo)
    with TestClient(app, base_url="http://testclient") as test_client:
        res = test_client.post(
            f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "max_total_cost": str(plan.total_cost + Decimal("100.00")),
                "currency": plan.currency,
                "expires_at": FUTURE.isoformat(),
            },
        )
        assert res.status_code == 503
        assert "unreachable" in res.json()["detail"]
    clear_jwk_cache()


def test_algorithm_confusion_attempts_rejected(client, setup_case_and_plan):
    case, plan = setup_case_and_plan
    payload_body = {
        "max_total_cost": str(plan.total_cost + Decimal("100.00")),
        "currency": plan.currency,
        "expires_at": FUTURE.isoformat(),
    }

    # 1. alg = "none" attack
    none_token = jwt.encode(
        {"sub": "admin", "aud": "authenticated", "exp": 9999999999, "app_metadata": {"roles": ["procurement_approver"]}},
        "",
        algorithm="none",
    )
    res_none = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {none_token}"},
        json=payload_body,
    )
    assert res_none.status_code == 401

    # 2. alg = "HS256" attack when server only allows ES256
    hs256_token = jwt.encode(
        {"sub": "admin", "aud": "authenticated", "exp": 9999999999, "app_metadata": {"roles": ["procurement_approver"]}},
        "secret",
        algorithm="HS256",
    )
    res_hs = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {hs256_token}"},
        json=payload_body,
    )
    assert res_hs.status_code == 401
    assert "disallowed" in res_hs.json()["detail"] or "unsupported" in res_hs.json()["detail"]


def test_asymmetric_operates_without_shared_secret(es256_settings):
    assert es256_settings.supabase_jwt_secret is None
    assert es256_settings.is_auth_configured() is True
    assert es256_settings.get_jwks_url() == "https://rklvrimqejqtzoyloawf.supabase.co/auth/v1/.well-known/jwks.json"
    assert es256_settings.get_jwt_issuer() == "https://rklvrimqejqtzoyloawf.supabase.co/auth/v1"


# ==============================================================================
# Authorization & Role Tests (App Metadata vs User Metadata)
# ==============================================================================

def test_roles_strictly_from_app_metadata(client, setup_case_and_plan, ec_keys):
    case, plan = setup_case_and_plan
    priv, _, _, kid = ec_keys
    payload_body = {
        "max_total_cost": str(plan.total_cost + Decimal("100.00")),
        "currency": plan.currency,
        "expires_at": FUTURE.isoformat(),
    }

    # Token with no role in app_metadata, but claims procurement_approver in user_metadata
    spoofed_token = generate_es256_jwt(
        priv,
        kid=kid,
        roles=[],
        user_metadata={"roles": ["procurement_approver"], "role": "admin"},
    )
    res = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {spoofed_token}"},
        json=payload_body,
    )
    assert res.status_code == 403
    assert "requires procurement_approver role" in res.json()["detail"]

    # Token with viewer role in app_metadata
    viewer_token = generate_es256_jwt(
        priv,
        kid=kid,
        roles=["viewer"],
    )
    res_v = client.post(
        f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json=payload_body,
    )
    assert res_v.status_code == 403
    assert "requires procurement_approver role" in res_v.json()["detail"]


# ==============================================================================
# Isolated Legacy HS256 Support Test
# ==============================================================================

def test_isolated_hs256_verification_when_configured(test_repo, setup_case_and_plan):
    case, plan = setup_case_and_plan
    hs_settings = Settings(
        database_backend="sqlite",
        warehouse_api_key=None,
        supabase_jwt_secret=JWT_SECRET,
        supabase_jwt_audience="authenticated",
        supabase_jwt_algorithms="HS256",
    )
    app = create_app(settings=hs_settings, repository=test_repo)
    with TestClient(app, base_url="http://testclient") as hs_client:
        token = generate_hs256_jwt(roles=["procurement_approver"])
        res = hs_client.post(
            f"/cases/{case.case_id}/plans/{plan.plan_id}/approvals",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "max_total_cost": str(plan.total_cost + Decimal("100.00")),
                "currency": plan.currency,
                "expires_at": FUTURE.isoformat(),
            },
        )
        assert res.status_code == 201


# ==============================================================================
# Real Project JWKS Integration Test (Non-destructive, never prints secrets)
# ==============================================================================

def test_real_supabase_project_jwks_connectivity():
    """Verify live JWKS connectivity to the real configured project endpoint."""
    import urllib.request

    url = "https://rklvrimqejqtzoyloawf.supabase.co/auth/v1/.well-known/jwks.json"
    req = urllib.request.Request(url, headers={"User-Agent": "StockRescue-Auth-Verification"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            assert response.status == 200
            import json
            data = json.loads(response.read().decode())
            assert "keys" in data
            assert len(data["keys"]) >= 1
            key = data["keys"][0]
            assert key.get("alg") == "ES256"
            assert "kid" in key
            assert key.get("crv") == "P-256"
    except Exception as exc:
        pytest.skip(f"Live network connectivity to Supabase JWKS unavailable: {exc}")
