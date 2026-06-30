"""P1.5: the Discord bot token must be stored encrypted in config.json, and
get_bot_token() must transparently decrypt it (while still accepting a legacy
plaintext token for backward compatibility)."""
from __future__ import annotations

import pytest


def test_get_bot_token_decrypts_encrypted_config(monkeypatch):
    from forven.secret_storage import encrypt_secret, is_encrypted_secret
    import forven.bot as bot

    ciphertext = encrypt_secret("my-real-bot-token")
    assert is_encrypted_secret(ciphertext)  # it really is encrypted at rest
    assert "my-real-bot-token" not in ciphertext

    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setattr(bot, "load_config", lambda: {"discord_token": ciphertext})

    assert bot.get_bot_token() == "my-real-bot-token"


def test_get_bot_token_accepts_legacy_plaintext(monkeypatch):
    import forven.bot as bot

    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setattr(bot, "load_config", lambda: {"discord_token": "legacy-plaintext-token"})

    assert bot.get_bot_token() == "legacy-plaintext-token"


def test_get_bot_token_env_var_wins(monkeypatch):
    import forven.bot as bot

    monkeypatch.setenv("DISCORD_TOKEN", "env-token")
    monkeypatch.setattr(bot, "load_config", lambda: {"discord_token": "ignored"})

    assert bot.get_bot_token() == "env-token"


def test_get_bot_token_missing_raises(monkeypatch):
    import forven.bot as bot

    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setattr(bot, "load_config", lambda: {})

    with pytest.raises(ValueError, match="Discord bot token not found"):
        bot.get_bot_token()
