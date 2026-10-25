from __future__ import annotations

import threading
from typing import Annotated, Any
import jwt
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKError
from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from shared.config import Settings
from shared.models import OperatorRole

_jwk_clients: dict[tuple[str, int, float], PyJWKClient] = {}
_jwk_lock = threading.Lock()


def get_jwk_client(
    jwks_url: str,
    *,
    cache_seconds: int = 300,
    timeout_seconds: float = 5.0,
) -> PyJWKClient:
    """Retrieve or create a thread-safe cached PyJWKClient for the given trusted JWKS endpoint."""
    key = (jwks_url, cache_seconds, timeout_seconds)
    with _jwk_lock:
        if key not in _jwk_clients:
            _jwk_clients[key] = PyJWKClient(
                jwks_url,
                cache_keys=True,
                max_cached_keys=16,
                lifespan=cache_seconds,
                timeout=timeout_seconds,
            )
        return _jwk_clients[key]


def clear_jwk_cache() -> None:
    """Clear all cached JWKS clients and public keys (used in tests)."""
    with _jwk_lock:
        _jwk_clients.clear()


class OperatorPrincipal(BaseModel):
    operator_id: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=255)
    roles: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)

    @property
    def approver_identity(self) -> str:
        """Return the stable human/operator identity for audit and approval records."""
        return self.email or self.operator_id

    def has_role(self, role: str | OperatorRole) -> bool:
        role_value = role.value if isinstance(role, OperatorRole) else str(role)
        target = role_value.strip().casefold()
        return any(r.strip().casefold() == target for r in self.roles)

    def can_approve_procurement(self) -> bool:
        return self.has_role(OperatorRole.PROCUREMENT_APPROVER) or self.has_role(OperatorRole.ADMIN)


def extract_bearer_token(authorization_header: str | None) -> str | None:
    if not authorization_header or not isinstance(authorization_header, str):
        return None
    parts = authorization_header.strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0].casefold() == "bearer":
        token = parts[1].strip()
        return token if token else None
    return None


def verify_supabase_jwt(token: str, settings: Settings) -> OperatorPrincipal:
    """Verify Supabase Auth JWT token server-side and resolve operator roles from trusted claims.

    - Supports ES256 asymmetric verification using project JWKS public keys.
    - Supports isolated HS256 symmetric verification if explicitly configured.
    - Fails closed if authentication is unconfigured, keys cannot be reached, or token is invalid.
    - Never passes SUPABASE_JWT_SECRET to ES256 verification or falls back between algorithms.
    - Roles are strictly resolved from server-controlled `app_metadata` or top-level custom claims,
      never from client-editable `user_metadata`.
    """
    if not settings.is_auth_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication service is not configured",
        )

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication token header is invalid",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        ) from exc

    alg = header.get("alg")
    if not alg or not isinstance(alg, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication token missing algorithm header",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )

    allowed_algorithms = [
        a.strip().upper()
        for a in settings.supabase_jwt_algorithms.split(",")
        if a.strip()
    ]
    alg_upper = alg.strip().upper()
    if alg_upper not in allowed_algorithms or alg_upper == "NONE":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"unsupported or disallowed signing algorithm: {alg}",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )

    payload: dict[str, Any]

    if alg_upper == "ES256":
        kid = header.get("kid")
        if not kid or not isinstance(kid, str) or not kid.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="ES256 token missing key id (kid) in header",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            )

        jwks_url = settings.get_jwks_url()
        issuer = settings.get_jwt_issuer()
        audience = settings.supabase_jwt_audience

        if not jwks_url or not issuer or not audience:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="asymmetric JWT verification is not properly configured",
            )

        try:
            jwk_client = get_jwk_client(
                jwks_url,
                cache_seconds=settings.supabase_jwks_cache_seconds,
                timeout_seconds=settings.supabase_jwks_timeout_seconds,
            )
            signing_key = jwk_client.get_signing_key_from_jwt(token)
            verification_key = signing_key.key
        except PyJWKClientConnectionError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="public key service is currently unreachable",
            ) from exc
        except (PyJWKError, Exception) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"matching public signing key was not found for kid: {kid}",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc

        try:
            payload = jwt.decode(
                token,
                verification_key,
                algorithms=["ES256"],
                audience=audience,
                issuer=issuer,
                options={
                    "require": ["exp", "sub", "iss", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token has expired",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\", error_description=\"token expired\""},
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token issuer is invalid",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token audience is invalid",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
        except jwt.MissingRequiredClaimError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"authentication token missing required claim: {exc}",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
        except jwt.InvalidSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token signature is invalid",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token is invalid",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc

    elif alg_upper == "HS256":
        # Strictly isolated symmetric verification path
        if not settings.supabase_jwt_secret or not settings.supabase_jwt_secret.get_secret_value().strip():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="symmetric JWT verification is not configured",
            )
        secret = settings.supabase_jwt_secret.get_secret_value()
        audience = settings.supabase_jwt_audience
        issuer = settings.get_jwt_issuer()

        decode_options: dict[str, Any] = {
            "require": ["exp", "sub"],
            "verify_signature": True,
            "verify_exp": True,
        }
        if audience:
            decode_options["require"].append("aud")
            decode_options["verify_aud"] = True
        if issuer:
            decode_options["require"].append("iss")
            decode_options["verify_iss"] = True

        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=audience,
                issuer=issuer,
                options=decode_options,
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token has expired",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\", error_description=\"token expired\""},
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token issuer is invalid",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token audience is invalid",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
        except jwt.MissingRequiredClaimError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"authentication token missing required claim: {exc}",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
        except jwt.InvalidSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token signature is invalid",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication token is invalid",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            ) from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"unsupported signing algorithm: {alg}",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )

    sub = str(payload.get("sub", "")).strip()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication token missing subject claim",
        )

    email = payload.get("email")
    if email is not None:
        email = str(email).strip() or None

    # Resolve roles ONLY from server-controlled app_metadata or trusted custom claims
    # NEVER resolve from user_metadata which is end-user editable via Supabase client SDK
    resolved_roles: list[str] = []

    app_metadata = payload.get("app_metadata")
    if isinstance(app_metadata, dict):
        raw_roles = app_metadata.get("roles")
        if isinstance(raw_roles, list):
            resolved_roles.extend(str(r).strip() for r in raw_roles if str(r).strip())
        elif isinstance(raw_roles, str) and raw_roles.strip():
            resolved_roles.append(raw_roles.strip())

        raw_role = app_metadata.get("role")
        if isinstance(raw_role, str) and raw_role.strip():
            resolved_roles.append(raw_role.strip())

    # Optional top-level user_roles claim
    custom_roles = payload.get("user_roles")
    if isinstance(custom_roles, list):
        resolved_roles.extend(str(r).strip() for r in custom_roles if str(r).strip())
    elif isinstance(custom_roles, str) and custom_roles.strip():
        resolved_roles.append(custom_roles.strip())

    unique_roles = list(dict.fromkeys(resolved_roles))

    return OperatorPrincipal(
        operator_id=sub,
        email=email,
        roles=unique_roles,
        claims=payload,
    )


async def require_authenticated_operator(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> OperatorPrincipal:
    """Dependency requiring a verified Bearer token from an authenticated operator."""
    token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication credentials required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings: Settings = request.app.state.settings
    return verify_supabase_jwt(token, settings)


async def require_procurement_approver(
    operator: Annotated[OperatorPrincipal, Depends(require_authenticated_operator)],
) -> OperatorPrincipal:
    """Dependency requiring an authenticated operator with procurement_approver or admin role."""
    if not operator.can_approve_procurement():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient permissions: requires procurement_approver role",
        )
    return operator
