"""
Conductor worker: Reads ranked_jobs.md and emails it to Jonathan.
Skips sending if the file hasn't been updated since last email.
"""
import os
import smtplib
import json
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(r"C:\Users\jarms\repos\skillmatch-mcp\data")
RANKED_FILE = DATA_DIR / "ranked_jobs.md"
LAST_EMAIL_FILE = DATA_DIR / "last_email_timestamp.txt"
EMAIL_TO = "jarmstrong158@gmail.com"


def should_send():
    """Only send if ranked_jobs.md has been modified since last email."""
    if not RANKED_FILE.exists():
        print("No ranked_jobs.md found. Skipping.")
        return False

    mod_time = RANKED_FILE.stat().st_mtime

    if LAST_EMAIL_FILE.exists():
        last_sent = float(LAST_EMAIL_FILE.read_text().strip())
        if mod_time <= last_sent:
            print("ranked_jobs.md unchanged since last email. Skipping.")
            return False

    return True


def send_email(subject, body):
    user = os.environ.get("GMAIL_USER", "")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        print("Email skipped — credentials not configured")
        return False
    try:
        msg = MIMEText(body, "plain")
        msg["From"] = user
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pw)
            s.sendmail(user, EMAIL_TO, msg.as_string())
        print(f"Email sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False


if __name__ == "__main__":
    if not should_send():
        exit(0)

    body = RANKED_FILE.read_text(encoding="utf-8")
    today = datetime.now().strftime("%m/%d/%Y")
    subject = f"Job Scout Rankings — {today}"

    if send_email(subject, body):
        # Record timestamp so we don't re-send the same report
        LAST_EMAIL_FILE.write_text(str(RANKED_FILE.stat().st_mtime))
        print("Done.")
    else:
        print("Failed to send email.")
        exit(1)
