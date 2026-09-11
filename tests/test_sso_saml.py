import base64
import json
import pytest
from vad.deploy.sso_saml import OIDCProvider, SAMLProvider, SSOMiddleware, AuthUser


def test_oidc_provider():
    provider = OIDCProvider(issuer_url="http://127.0.0.1:8081", client_id="my-client")
    url = provider.get_login_url("http://127.0.0.1:8082/callback")
    assert "http://127.0.0.1:8081/oauth/authorize" in url
    assert "client_id=my-client" in url

    claims = {"sub": "usr-123", "email": "alice@acme.com", "name": "Alice", "roles": ["admin"]}
    claims_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("utf-8").rstrip("=")
    dummy_jwt = f"header.{claims_b64}.signature"

    user = provider.authenticate(f"Bearer {dummy_jwt}")
    assert user is not None
    assert user.user_id == "usr-123"
    assert user.email == "alice@acme.com"
    assert "admin" in user.roles
    assert user.provider == "oidc"


def test_saml_provider():
    provider = SAMLProvider(idp_sso_url="http://127.0.0.1:8083/idp")
    url = provider.get_login_url("http://127.0.0.1:8082/saml/acs")
    assert "http://127.0.0.1:8083/idp?RelayState=" in url

    saml_xml = """
    <samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol">
        <saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
            <saml:Subject>
                <saml:NameID>bob@acme.com</saml:NameID>
            </saml:Subject>
        </saml:Assertion>
    </samlp:Response>
    """
    saml_b64 = base64.b64encode(saml_xml.encode("utf-8")).decode("utf-8")

    user = provider.authenticate(saml_b64)
    assert user is not None
    assert user.email == "bob@acme.com"
    assert user.provider == "saml"


def test_sso_middleware():
    provider = OIDCProvider()
    middleware = SSOMiddleware(provider=provider)

    assert middleware.verify_request(None) is None
    assert middleware.verify_request("Bearer invalid-token") is None
