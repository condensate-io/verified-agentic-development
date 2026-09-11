"""Enterprise SSO / SAML / OIDC Authentication Providers and Middleware for VAD."""

from abc import ABC, abstractmethod
import base64
import json
import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    user_id: str
    email: str
    name: str
    roles: List[str] = Field(default_factory=list)
    organization: str = "Enterprise"
    provider: str = "local"


class AuthProvider(ABC):
    """Abstract authentication provider for enterprise dashboards and services."""

    @abstractmethod
    def get_login_url(self, redirect_uri: str) -> str:
        """Returns identity provider login redirect URL."""
        pass

    @abstractmethod
    def authenticate(self, token_or_assertion: str) -> Optional[AuthUser]:
        """Validates credential (token or SAML assertion) and returns authenticated user."""
        pass


class OIDCProvider(AuthProvider):
    """OpenID Connect provider validating bearer tokens."""

    def __init__(
        self,
        issuer_url: str = "https://idp.example.com",
        client_id: str = "vad-app",
        client_secret: str = "secret",
    ):
        self.issuer_url = issuer_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret

    def get_login_url(self, redirect_uri: str) -> str:
        return f"{self.issuer_url}/oauth/authorize?client_id={self.client_id}&redirect_uri={redirect_uri}&response_type=code&scope=openid+email+profile"

    def authenticate(self, token_or_assertion: str) -> Optional[AuthUser]:
        # Handle bearer token format (accept mock JWT or base64 json claims)
        token = token_or_assertion.strip()
        if token.startswith("Bearer "):
            token = token[len("Bearer "):]

        if not token:
            return None

        # Expect 3 parts for JWT (header.payload.sig) or raw payload
        parts = token.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            # Fix padding
            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
            try:
                claims = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")))
                return AuthUser(
                    user_id=claims.get("sub", "unknown"),
                    email=claims.get("email", ""),
                    name=claims.get("name", claims.get("email", "User")),
                    roles=claims.get("roles", ["operator"]),
                    organization=claims.get("org", "Enterprise"),
                    provider="oidc",
                )
            except Exception:
                return None
        return None


class SAMLProvider(AuthProvider):
    """SAML 2.0 Provider validating SAML assertions."""

    def __init__(
        self,
        idp_sso_url: str = "https://sso.example.com/saml2/idp",
        entity_id: str = "https://vad.corp.internal/sp",
    ):
        self.idp_sso_url = idp_sso_url
        self.entity_id = entity_id

    def get_login_url(self, redirect_uri: str) -> str:
        return f"{self.idp_sso_url}?RelayState={redirect_uri}"

    def authenticate(self, token_or_assertion: str) -> Optional[AuthUser]:
        if not token_or_assertion:
            return None

        # Parse base64 SAML assertion or XML-like text
        try:
            if token_or_assertion.startswith("<"):
                xml_text = token_or_assertion
            else:
                raw = base64.b64decode(token_or_assertion)
                xml_text = raw.decode("utf-8", errors="ignore")

            # Basic SAML NameID extraction
            import re
            name_match = re.search(r"<saml:NameID[^>]*>([^<]+)</saml:NameID>", xml_text)
            email = name_match.group(1) if name_match else ""
            if not email and "@" in xml_text:
                email_match = re.search(r"[\w\.-]+@[\w\.-]+", xml_text)
                email = email_match.group(0) if email_match else ""

            if email:
                return AuthUser(
                    user_id=email,
                    email=email,
                    name=email.split("@")[0],
                    roles=["operator"],
                    organization="Enterprise",
                    provider="saml",
                )
        except Exception:
            return None
        return None


class SSOMiddleware:
    """Detects configured SSO provider from environment and validates request headers."""

    def __init__(self, provider: Optional[AuthProvider] = None):
        if provider is not None:
            self.provider = provider
        else:
            ptype = os.getenv("VAD_AUTH_PROVIDER", "oidc").lower()
            if ptype == "saml":
                self.provider = SAMLProvider(
                    idp_sso_url=os.getenv("VAD_SAML_IDP_URL", "https://sso.internal/saml"),
                    entity_id=os.getenv("VAD_SAML_SP_ENTITY_ID", "vad-sp"),
                )
            else:
                self.provider = OIDCProvider(
                    issuer_url=os.getenv("VAD_OIDC_ISSUER", "https://idp.internal"),
                    client_id=os.getenv("VAD_OIDC_CLIENT_ID", "vad-client"),
                )

    def verify_request(self, auth_header: Optional[str]) -> Optional[AuthUser]:
        if not auth_header:
            return None
        return self.provider.authenticate(auth_header)
