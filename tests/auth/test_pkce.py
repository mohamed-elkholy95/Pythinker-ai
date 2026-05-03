import base64
import hashlib

from pythinker.auth.pkce import generate_pkce_pair


def test_returns_verifier_and_challenge():
    verifier, challenge = generate_pkce_pair()
    assert isinstance(verifier, str)
    assert isinstance(challenge, str)


def test_verifier_is_url_safe_and_long_enough():
    verifier, _ = generate_pkce_pair()
    # RFC 7636: 43-128 chars, [A-Z][a-z][0-9]-._~
    assert 43 <= len(verifier) <= 128
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
    assert set(verifier).issubset(allowed)


def test_challenge_is_s256_of_verifier():
    verifier, challenge = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_unique_per_call():
    a, _ = generate_pkce_pair()
    b, _ = generate_pkce_pair()
    assert a != b
