"""Tests for forven.redact — secret scrubbing in tool output."""

from forven.redact import REDACTED_MARKER, redact, redact_dict


def test_openai_key_scrubbed():
    text = "Here is my key: sk-proj-abcdefghij1234567890ABCDEFGHIJ for testing"
    scrubbed, count = redact(text)
    assert "sk-proj-abcdefghij1234567890ABCDEFGHIJ" not in scrubbed
    assert REDACTED_MARKER in scrubbed
    assert count == 1


def test_anthropic_key_scrubbed():
    text = "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghij1234567890ABCDEFGHIJ"
    scrubbed, count = redact(text)
    assert "sk-ant-api03-abcdefghij1234567890ABCDEFGHIJ" not in scrubbed
    # The env-var pattern matches the assignment too, so we expect at least 1.
    assert count >= 1


def test_bearer_token_scrubbed():
    text = "Authorization: Bearer abc123def456ghi789jkl012mno345"
    scrubbed, count = redact(text)
    assert "abc123def456ghi789jkl012mno345" not in scrubbed
    assert "Bearer" in scrubbed  # token type preserved
    assert REDACTED_MARKER in scrubbed
    assert count == 1


def test_slack_token_scrubbed():
    text = "Slack token: xoxb-1234567890-abcdefghij"
    scrubbed, count = redact(text)
    assert "xoxb-1234567890-abcdefghij" not in scrubbed
    assert count == 1


def test_oauth_and_key_json_fields_scrubbed():
    """P1.5: the JSON-value redactor must cover the OAuth + key field names this
    app actually stores secrets under (access/refresh/id_token/token/private_key/
    api_secret) — the pre-P1.5 list omitted them."""
    cases = {
        '"access": "ya29-secret-access-token-value"': "ya29-secret-access-token-value",
        '"refresh": "1-refresh-token-secret-value-xyz"': "1-refresh-token-secret-value-xyz",
        '"id_token": "header-payloadsegment-signature"': "header-payloadsegment-signature",
        '"token": "rawtokensecretvalue1234567890"': "rawtokensecretvalue1234567890",
        '"private_key": "deadbeefcafef00d-not-a-real-key"': "deadbeefcafef00d-not-a-real-key",
        '"api_secret": "supersecretapivalue0987654321"': "supersecretapivalue0987654321",
    }
    for text, secret in cases.items():
        scrubbed, count = redact(text)
        assert secret not in scrubbed, f"leaked secret in: {text}"
        assert REDACTED_MARKER in scrubbed
        assert count >= 1


def test_github_token_scrubbed():
    text = "GH token: ghp_abcdefghij1234567890ABCDEFGHIJ123456"
    scrubbed, count = redact(text)
    assert "ghp_abcdefghij1234567890ABCDEFGHIJ123456" not in scrubbed
    assert count == 1


def test_aws_access_key_scrubbed():
    text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    scrubbed, count = redact(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed
    assert count >= 1


def test_jwt_scrubbed():
    text = (
        "Token: eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "abcdefghij1234567890ABC"
    )
    scrubbed, count = redact(text)
    assert "eyJhbGciOiJIUzI1NiJ9" not in scrubbed
    assert count == 1


def test_env_var_assignment():
    text = "OPENAI_API_KEY=hunter2"
    scrubbed, count = redact(text)
    assert "hunter2" not in scrubbed
    assert "OPENAI_API_KEY" in scrubbed  # var name preserved
    assert count == 1


def test_env_var_assignment_with_quotes():
    text = 'export DB_PASSWORD="my-secret-password-123"'
    scrubbed, count = redact(text)
    assert "my-secret-password-123" not in scrubbed
    assert count == 1


def test_json_key_scrubbed():
    text = '{"api_key": "sk-real-secret-value-12345"}'
    scrubbed, count = redact(text)
    assert "sk-real-secret-value-12345" not in scrubbed
    assert "api_key" in scrubbed  # key name preserved
    assert REDACTED_MARKER in scrubbed
    assert count >= 1


def test_json_authorization_scrubbed():
    text = '{"authorization": "Bearer abc123def456ghi789jkl012mno345"}'
    scrubbed, count = redact(text)
    assert "abc123def456ghi789jkl012mno345" not in scrubbed
    assert count >= 1


def test_no_false_positive_on_password_field_var():
    """Common code pattern: declaring a form field named 'password' is NOT a leak."""
    text = "password_field = forms.CharField(widget=forms.PasswordInput)"
    scrubbed, count = redact(text)
    assert scrubbed == text
    assert count == 0


def test_no_false_positive_on_short_strings():
    """Short alphanumeric strings should not match secret patterns."""
    text = "x = 5; sk-1; tok-abc; key-12"
    scrubbed, count = redact(text)
    assert scrubbed == text
    assert count == 0


def test_empty_input():
    assert redact("") == ("", 0)
    assert redact(None)[1] == 0  # type: ignore[arg-type]  # graceful


def test_idempotent():
    text = "API_KEY=sk-test-1234567890abcdefABCDEF"
    once, _ = redact(text)
    twice, count = redact(once)
    assert once == twice
    assert count == 0


def test_redact_dict_deep_walk():
    obj = {
        "user": "alice",
        "credentials": {
            "api_key": "sk-real-secret-value-12345",
            "headers": ["Authorization: Bearer abc123def456ghi789jkl012"],
        },
        "count": 42,
        "tags": ("public", "OPENAI_API_KEY=hunter2"),
    }
    scrubbed, count = redact_dict(obj)
    assert scrubbed["user"] == "alice"
    assert scrubbed["count"] == 42
    assert "sk-real-secret-value-12345" not in str(scrubbed)
    assert "hunter2" not in str(scrubbed)
    assert "abc123def456ghi789jkl012" not in str(scrubbed)
    assert isinstance(scrubbed["tags"], tuple)
    assert count >= 3


def test_redact_dict_returns_new_object():
    """Original object must not be mutated."""
    obj = {"key": "API_KEY=hunter2"}
    redact_dict(obj)
    assert obj["key"] == "API_KEY=hunter2"


def test_multiple_secrets_in_one_string():
    text = (
        "Two leaks: sk-proj-abcdefghij1234567890ABCDEFGHIJ "
        "and Bearer xyz123456789012345678901"
    )
    scrubbed, count = redact(text)
    assert "sk-proj-abcdefghij1234567890ABCDEFGHIJ" not in scrubbed
    assert "xyz123456789012345678901" not in scrubbed
    assert count == 2
