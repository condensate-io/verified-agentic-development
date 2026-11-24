import base64
import json
import os
import stat
import pytest

from vad.review.dsse_attestation import (
    DSSEAttestor,
    DSSEEnvelope,
    LEGACY_HMAC_SECRET,
    decode_attestation_statement,
)
from vad.signing import ed25519_local
from vad.signing.ed25519_local import (
    DSSEKeyProvider,
    ExplicitHMACKeyProvider,
    LocalEd25519KeyProvider,
    ed25519_sign,
    ed25519_verify,
    pure_ed25519_public_key_from_seed,
    pure_ed25519_sign,
    pure_ed25519_verify,
)


# Official RFC 8032 Section 7.1 Test Vector 1 (empty message)
RFC8032_TV1_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
RFC8032_TV1_PUBKEY = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
)
RFC8032_TV1_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)

# Official RFC 8032 Section 7.1 Test Vector 2 (1-byte message 0x72)
RFC8032_TV2_SEED = bytes.fromhex(
    "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"
)
RFC8032_TV2_PUBKEY = bytes.fromhex(
    "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"
)
RFC8032_TV2_MSG = bytes.fromhex("72")
RFC8032_TV2_SIG = bytes.fromhex(
    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
    "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
)


def test_pure_python_ed25519_matches_rfc8032_vectors():
    assert pure_ed25519_public_key_from_seed(RFC8032_TV1_SEED) == RFC8032_TV1_PUBKEY
    sig1 = pure_ed25519_sign(RFC8032_TV1_SEED, b"")
    assert sig1 == RFC8032_TV1_SIG
    assert pure_ed25519_verify(RFC8032_TV1_PUBKEY, b"", sig1) is True
    assert pure_ed25519_verify(RFC8032_TV1_PUBKEY, b"x", sig1) is False

    assert pure_ed25519_public_key_from_seed(RFC8032_TV2_SEED) == RFC8032_TV2_PUBKEY
    sig2 = pure_ed25519_sign(RFC8032_TV2_SEED, RFC8032_TV2_MSG)
    assert sig2 == RFC8032_TV2_SIG
    assert pure_ed25519_verify(RFC8032_TV2_PUBKEY, RFC8032_TV2_MSG, sig2) is True


def test_ed25519_fallback_when_cryptography_unavailable(monkeypatch):
    monkeypatch.setattr(ed25519_local, "_HAS_CRYPTOGRAPHY", False)
    provider = LocalEd25519KeyProvider.from_seed(RFC8032_TV1_SEED, key_id="rfc8032-tv1")
    assert provider.public_key == RFC8032_TV1_PUBKEY
    sig = ed25519_sign(RFC8032_TV1_SEED, b"")
    assert sig == RFC8032_TV1_SIG
    assert ed25519_verify(RFC8032_TV1_PUBKEY, b"", sig) is True

    # Tampered signature rejected
    bad_sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
    assert ed25519_verify(RFC8032_TV1_PUBKEY, b"", bad_sig) is False


def test_local_ed25519_key_provider_autogeneration_reload_and_rotation(tmp_path, monkeypatch):
    key_dir = tmp_path / "keys"
    provider1 = LocalEd25519KeyProvider(key_dir=key_dir)
    assert isinstance(provider1, DSSEKeyProvider)
    assert provider1.algorithm == "ed25519"

    key_file = key_dir / "dsse_ed25519.json"
    assert key_file.is_file()
    data = json.loads(key_file.read_text(encoding="utf-8"))
    assert data["key_id"] == provider1.key_id
    assert base64.b64decode(data["public_key"]) == provider1.public_key
    assert len(base64.b64decode(data["private_key"])) == 32
    if os.name == "posix":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    # Reload across instances shares the exact same keypair
    provider2 = LocalEd25519KeyProvider(key_dir=key_dir)
    assert provider2.key_id == provider1.key_id
    assert provider2.public_key == provider1.public_key

    pae = DSSEAttestor.pre_authentication_encoding("application/vnd.in-toto+json", b'{"ok":true}')
    sig = provider1.sign_pae(pae)
    assert provider2.verify_pae(pae, sig) is True

    # Tampered signature rejected
    tampered_sig = bytes([sig[0] ^ 0x01]) + sig[1:]
    assert provider2.verify_pae(pae, tampered_sig) is False

    # Wrong-key rejection via deterministic seed
    other = LocalEd25519KeyProvider.from_seed(RFC8032_TV2_SEED, key_id="other-key")
    assert other.verify_pae(pae, sig) is False

    # Key rotation generates new keypair, persists it, and rejects old signatures
    rotated = provider1.rotate()
    assert rotated.key_id != provider1.key_id
    assert rotated.public_key != provider1.public_key
    assert rotated.verify_pae(pae, sig) is False

    reloaded_after_rotate = LocalEd25519KeyProvider(key_dir=key_dir)
    assert reloaded_after_rotate.key_id == rotated.key_id
    assert reloaded_after_rotate.public_key == rotated.public_key
    new_sig = rotated.sign_pae(pae)
    assert reloaded_after_rotate.verify_pae(pae, new_sig) is True

    # Default .vad/keys directory works in an isolated cwd
    monkeypatch.chdir(tmp_path)
    default_attestor1 = DSSEAttestor()
    default_attestor2 = DSSEAttestor()
    env = default_attestor1.sign(b"hello default ed25519")
    assert (tmp_path / ".vad" / "keys" / "dsse_ed25519.json").is_file()
    valid, decoded, err = default_attestor2.verify(env)
    assert valid is True
    assert decoded == b"hello default ed25519"
    assert err is None


def test_explicit_hmac_key_provider_validation():
    with pytest.raises(ValueError, match="secret_key"):
        ExplicitHMACKeyProvider("hmac-1", b"")
    with pytest.raises(ValueError, match="key_id"):
        ExplicitHMACKeyProvider("", b"non-empty-secret")

    provider = ExplicitHMACKeyProvider("hmac-1", b"explicit-secret-key")
    assert isinstance(provider, DSSEKeyProvider)
    assert provider.algorithm == "hmac-sha256"
    sig = provider.sign_pae(b"test-pae")
    assert provider.verify_pae(b"test-pae", sig) is True
    assert provider.verify_pae(b"test-pae", b"wrong") is False


def test_dsse_attestor_strict_mode_and_ed25519_roundtrip(tmp_path):
    attestor = DSSEAttestor(key_dir=tmp_path, strict=True)
    verifier = DSSEAttestor(key_dir=tmp_path, strict=True)

    # Strict mode rejects legacy hardcoded HMAC secret on all entrypoints
    with pytest.raises(ValueError, match="strict mode"):
        attestor.sign(b"payload", secret_key=LEGACY_HMAC_SECRET)

    env = attestor.sign(b'{"check":"passed"}')
    assert env.signatures[0].keyid == attestor.key_provider.key_id

    with pytest.raises(ValueError, match="strict mode"):
        verifier.verify(env, secret_key=LEGACY_HMAC_SECRET)

    with pytest.raises(ValueError, match="strict mode"):
        attestor.create_review_attestation(
            commit_sha="a" * 40,
            review_decision="approved",
            reviewer_agent="independent-verifier",
            proof_summary={"unit": "pass"},
            secret_key=LEGACY_HMAC_SECRET,
        )

    with pytest.raises(ValueError, match="strict mode"):
        verifier.verify_review_attestation(env, "a" * 40, secret_key=LEGACY_HMAC_SECRET)

    # Ed25519 round-trip succeeds across distinct instances sharing key_dir
    valid, payload_bytes, err = verifier.verify(env)
    assert valid is True
    assert payload_bytes == b'{"check":"passed"}'
    assert err is None

    # Wrong-key attestor rejects envelope
    wrong_verifier = DSSEAttestor(key_dir=tmp_path / "other_keys", strict=True)
    valid_wrong, _, err_wrong = wrong_verifier.verify(env)
    assert valid_wrong is False
    assert err_wrong == "Signature mismatch"

    # Tampered signature rejected
    tampered_sig = base64.b64encode(b"\x00" * 64).decode("utf-8")
    tampered_env = DSSEEnvelope(
        payloadType=env.payloadType,
        payload=env.payload,
        signatures=[env.signatures[0].model_copy(update={"sig": tampered_sig})],
    )
    valid_tampered, _, err_tampered = verifier.verify(tampered_env)
    assert valid_tampered is False
    assert err_tampered == "Signature mismatch"


def test_enriched_review_attestation_and_explicit_hmac_compatibility(tmp_path):
    attestor = DSSEAttestor(key_dir=tmp_path, strict=True)
    commit_sha = "1234567890abcdef1234567890abcdef12345678"

    envelope = attestor.create_review_attestation(
        commit_sha=commit_sha,
        review_decision="approved",
        reviewer_agent="adversarial-verifier",
        proof_summary={"tests": "passed", "lint": "passed"},
        eip_digest="sha256:eip-abc123",
        proof_plan_digest="sha256:plan-def456",
        assurance_level="A3_ATTESTED",
        verified_at="2026-11-24T01:00:00Z",
        expires_at="2026-11-25T01:00:00Z",
    )

    assert attestor.verify_review_attestation(envelope, commit_sha) is True
    assert attestor.verify_review_attestation(envelope, "0" * 40) is False

    stmt = decode_attestation_statement(envelope)
    assert stmt["_type"].endswith("/Statement/v1")
    assert stmt["predicateType"].endswith("/attestation/review/v1")
    assert stmt["subject"] == [{"name": "git-commit", "digest": {"sha1": commit_sha}}]
    predicate = stmt["predicate"]
    assert predicate["decision"] == "approved"
    assert predicate["reviewer"] == "adversarial-verifier"
    assert predicate["eip_digest"] == "sha256:eip-abc123"
    assert predicate["proof_plan_digest"] == "sha256:plan-def456"
    assert predicate["assurance_level"] == "A3_ATTESTED"
    assert predicate["verified_at"] == "2026-11-24T01:00:00Z"
    assert predicate["expires_at"] == "2026-11-25T01:00:00Z"

    # Explicit non-legacy HMAC remains supported via secret_key kwarg and ExplicitHMACKeyProvider
    explicit_secret = b"custom-non-legacy-hmac-secret-999"
    hmac_env = attestor.create_review_attestation(
        commit_sha=commit_sha,
        review_decision="approved",
        reviewer_agent="hmac-verifier",
        proof_summary={"unit": "pass"},
        secret_key=explicit_secret,
    )
    assert attestor.verify_review_attestation(hmac_env, commit_sha, secret_key=explicit_secret) is True

    hmac_provider = ExplicitHMACKeyProvider("hmac-explicit", explicit_secret)
    provider_attestor = DSSEAttestor(key_provider=hmac_provider, strict=True)
    assert provider_attestor.verify_review_attestation(hmac_env, commit_sha) is True
