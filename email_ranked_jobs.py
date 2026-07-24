"""
Conductor worker: Reads ranked_jobs.md (written by the scheduled ranker task)
and emails it. Caps the email body at the top 15 ranked entries.

Credentials are NEVER hardcoded here. They are loaded from
data/email_config.json (gitignored), with environment variables as a fallback.
The config file is the primary mechanism because environment variables are not
reliably inherited when the worker process is spawned by Conductor on Windows.
See README: Email Worker Setup.
"""
import json
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
RANKED_FILE = DATA_DIR / "ranked_jobs.md"
CONFIG_FILE = DATA_DIR / "email_config.json"
EXAMPLE_CONFIG_FILE = DATA_DIR / "email_config.example.json"

DEFAULT_EMAIL_CAP = 15


class EmailConfigError(Exception):
    """Raised when Gmail credentials cannot be resolved."""


def _setup_help(config_file=CONFIG_FILE):
    return (
        "Set up credentials by copying the tracked template and filling it in:\n"
        "\n"
        f"    copy {EXAMPLE_CONFIG_FILE}\n"
        f"      -> {config_file}\n"
        "\n"
        "Required keys: gmail_user, gmail_app_password.\n"
        f"Optional keys: email_to (defaults to gmail_user), email_cap (defaults to {DEFAULT_EMAIL_CAP}).\n"
        "The data/ folder is gitignored, so the real config is never committed.\n"
        "\n"
        "Generate an App Password at: Google Account -> Security -> 2-Step\n"
        "Verification -> App Passwords (choose 'Mail'). This is NOT your normal\n"
        "account password.\n"
        "\n"
        "Alternatively, set the GMAIL_USER and GMAIL_APP_PASSWORD environment\n"
        "variables (optionally EMAIL_TO / EMAIL_CAP). Note that env vars are often\n"
        "NOT inherited when this script is launched as a Conductor worker on\n"
        "Windows -- prefer the config file."
    )


def load_email_config(config_file=CONFIG_FILE, env=None):
    """Resolve email settings from data/email_config.json, then env vars.

    Returns a dict with gmail_user, gmail_app_password, email_to, email_cap.
    Raises EmailConfigError with actionable instructions if credentials are
    missing or the config file is unreadable.
    """
    env = os.environ if env is None else env
    config_file = Path(config_file)

    file_cfg = {}
    if config_file.exists():
        try:
            file_cfg = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise EmailConfigError(
                f"Could not read email config at {config_file}: {e}\n\n"
                + _setup_help(config_file)
            )
        if not isinstance(file_cfg, dict):
            raise EmailConfigError(
                f"Email config at {config_file} must be a JSON object.\n\n"
                + _setup_help(config_file)
            )

    def pick(key, env_key):
        value = file_cfg.get(key) or env.get(env_key) or ""
        return value.strip() if isinstance(value, str) else value

    gmail_user = pick("gmail_user", "GMAIL_USER")
    gmail_app_password = pick("gmail_app_password", "GMAIL_APP_PASSWORD")
    email_to = pick("email_to", "EMAIL_TO") or gmail_user

    missing = []
    if not gmail_user:
        missing.append("gmail_user (env: GMAIL_USER)")
    if not gmail_app_password:
        missing.append("gmail_app_password (env: GMAIL_APP_PASSWORD)")
    if missing:
        source = (
            f"Checked {config_file} and environment variables."
            if config_file.exists()
            else f"No config file found at {config_file}, and environment variables are unset."
        )
        raise EmailConfigError(
            "Missing Gmail credentials: " + ", ".join(missing) + "\n"
            + source + "\n\n"
            + _setup_help(config_file)
        )

    raw_cap = file_cfg.get("email_cap", env.get("EMAIL_CAP", DEFAULT_EMAIL_CAP))
    try:
        email_cap = int(raw_cap)
    except (TypeError, ValueError):
        email_cap = DEFAULT_EMAIL_CAP
    if email_cap < 1:
        email_cap = DEFAULT_EMAIL_CAP

    return {
        "gmail_user": gmail_user,
        "gmail_app_password": gmail_app_password,
        "email_to": email_to,
        "email_cap": email_cap,
    }


def cap_ranked_content(body, cap=DEFAULT_EMAIL_CAP):
    entries = re.split(r'\n(?=\d+\.\s)', body)
    if len(entries) <= 1:
        return body
    header = entries[0]
    ranked_entries = entries[1:]
    if len(ranked_entries) <= cap:
        return body
    capped = header + "\n".join(ranked_entries[:cap])
    capped += f"\n\n({len(ranked_entries) - cap} more ranked listings not shown in email)"
    return capped


def send_email(subject, body, config):
    gmail_user = config["gmail_user"]
    gmail_app_password = config["gmail_app_password"]
    email_to = config["email_to"]
    try:
        msg = MIMEText(body, "plain")
        msg["From"] = gmail_user
        msg["To"] = email_to
        msg["Subject"] = subject
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(gmail_user, gmail_app_password)
            s.sendmail(gmail_user, email_to, msg.as_string())
        print(f"Email sent to {email_to}")
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False


if __name__ == "__main__":
    if not RANKED_FILE.exists():
        print("No ranked_jobs.md found.")
        sys.exit(0)

    try:
        config = load_email_config()
    except EmailConfigError as e:
        print(f"ERROR: {e}")
        sys.exit(2)

    body = RANKED_FILE.read_text(encoding="utf-8")
    body = cap_ranked_content(body, config["email_cap"])

    today = datetime.now().strftime("%m/%d/%Y")
    subject = f"Job Scout Rankings -- {today}"

    if send_email(subject, body, config):
        RANKED_FILE.unlink()
        print("Done.")
    else:
        print("Failed.")
        sys.exit(1)
