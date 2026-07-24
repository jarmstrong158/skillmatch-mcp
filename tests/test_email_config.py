"""
Tests for email_ranked_jobs.py credential loading.

Guards the fix that removed hardcoded Gmail credentials: config file is the
primary source, environment variables are the fallback, and a missing config
raises an actionable error instead of failing silently.
"""

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import email_ranked_jobs as erj  # noqa: E402


def _write_config(tmp_path, payload):
    path = tmp_path / "email_config.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestLoadEmailConfig:
    def test_reads_from_config_file(self, tmp_path):
        cfg_path = _write_config(tmp_path, {
            "gmail_user": "a@gmail.com",
            "gmail_app_password": "aaaa bbbb cccc dddd",
            "email_to": "b@gmail.com",
            "email_cap": 5,
        })
        cfg = erj.load_email_config(cfg_path, env={})
        assert cfg == {
            "gmail_user": "a@gmail.com",
            "gmail_app_password": "aaaa bbbb cccc dddd",
            "email_to": "b@gmail.com",
            "email_cap": 5,
        }

    def test_config_file_wins_over_env(self, tmp_path):
        cfg_path = _write_config(tmp_path, {
            "gmail_user": "file@gmail.com",
            "gmail_app_password": "file-pw",
        })
        env = {"GMAIL_USER": "env@gmail.com", "GMAIL_APP_PASSWORD": "env-pw"}
        cfg = erj.load_email_config(cfg_path, env=env)
        assert cfg["gmail_user"] == "file@gmail.com"
        assert cfg["gmail_app_password"] == "file-pw"

    def test_env_fallback_when_no_config_file(self, tmp_path):
        env = {
            "GMAIL_USER": "env@gmail.com",
            "GMAIL_APP_PASSWORD": "env-pw",
            "EMAIL_TO": "to@gmail.com",
        }
        cfg = erj.load_email_config(tmp_path / "email_config.json", env=env)
        assert cfg["gmail_user"] == "env@gmail.com"
        assert cfg["gmail_app_password"] == "env-pw"
        assert cfg["email_to"] == "to@gmail.com"

    def test_env_fills_gaps_in_partial_config(self, tmp_path):
        cfg_path = _write_config(tmp_path, {"gmail_user": "file@gmail.com"})
        cfg = erj.load_email_config(cfg_path, env={"GMAIL_APP_PASSWORD": "env-pw"})
        assert cfg["gmail_user"] == "file@gmail.com"
        assert cfg["gmail_app_password"] == "env-pw"

    def test_email_to_defaults_to_gmail_user(self, tmp_path):
        cfg_path = _write_config(tmp_path, {
            "gmail_user": "a@gmail.com",
            "gmail_app_password": "pw",
        })
        cfg = erj.load_email_config(cfg_path, env={})
        assert cfg["email_to"] == "a@gmail.com"

    def test_email_cap_defaults_and_rejects_garbage(self, tmp_path):
        cfg_path = _write_config(tmp_path, {
            "gmail_user": "a@gmail.com",
            "gmail_app_password": "pw",
            "email_cap": "not-a-number",
        })
        assert erj.load_email_config(cfg_path, env={})["email_cap"] == erj.DEFAULT_EMAIL_CAP

        cfg_path = _write_config(tmp_path, {
            "gmail_user": "a@gmail.com",
            "gmail_app_password": "pw",
            "email_cap": 0,
        })
        assert erj.load_email_config(cfg_path, env={})["email_cap"] == erj.DEFAULT_EMAIL_CAP

    def test_missing_everything_raises_actionable_error(self, tmp_path):
        with pytest.raises(erj.EmailConfigError) as exc:
            erj.load_email_config(tmp_path / "email_config.json", env={})
        message = str(exc.value)
        assert "gmail_user" in message
        assert "gmail_app_password" in message
        assert "email_config.example.json" in message
        assert "App Password" in message

    def test_missing_password_only_raises(self, tmp_path):
        cfg_path = _write_config(tmp_path, {"gmail_user": "a@gmail.com"})
        with pytest.raises(erj.EmailConfigError) as exc:
            erj.load_email_config(cfg_path, env={})
        assert "gmail_app_password" in str(exc.value)

    def test_malformed_json_raises(self, tmp_path):
        cfg_path = _write_config(tmp_path, "{not json")
        with pytest.raises(erj.EmailConfigError) as exc:
            erj.load_email_config(cfg_path, env={})
        assert "Could not read" in str(exc.value)

    def test_non_object_json_raises(self, tmp_path):
        cfg_path = _write_config(tmp_path, ["a", "b"])
        with pytest.raises(erj.EmailConfigError) as exc:
            erj.load_email_config(cfg_path, env={})
        assert "must be a JSON object" in str(exc.value)

    def test_blank_values_are_treated_as_missing(self, tmp_path):
        cfg_path = _write_config(tmp_path, {
            "gmail_user": "   ",
            "gmail_app_password": "",
        })
        with pytest.raises(erj.EmailConfigError):
            erj.load_email_config(cfg_path, env={})


class TestNoHardcodedCredentials:
    def test_source_has_no_credential_literals(self):
        source = (SCRIPT_DIR / "email_ranked_jobs.py").read_text(encoding="utf-8")
        assert not re.search(r'^\s*GMAIL_APP_PASSWORD\s*=\s*["\']', source, re.M)
        assert not re.search(r'^\s*GMAIL_USER\s*=\s*["\']', source, re.M)
        assert "@gmail.com" not in source

    def test_example_config_documents_the_shape(self):
        example = json.loads(
            (SCRIPT_DIR / "data" / "email_config.example.json").read_text(encoding="utf-8")
        )
        for key in ("gmail_user", "gmail_app_password", "email_to", "email_cap"):
            assert key in example


class TestCapRankedContent:
    def test_caps_to_requested_number_of_entries(self):
        body = "Header\n" + "\n".join(f"{i}. Job {i}" for i in range(1, 21))
        capped = erj.cap_ranked_content(body, cap=5)
        assert "5. Job 5" in capped
        assert "6. Job 6" not in capped
        assert "15 more ranked listings not shown" in capped

    def test_under_cap_returns_body_unchanged(self):
        body = "Header\n1. Job 1\n2. Job 2"
        assert erj.cap_ranked_content(body, cap=15) == body
