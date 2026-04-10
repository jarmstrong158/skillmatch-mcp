"""
Conductor worker: Reads ranked_jobs.md (written by the scheduled ranker task)
and emails it. Only sends if the file has been updated since the last email.
Caps the email body at the top 15 ranked entries.
"""
import os
import re
import smtplib
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(r"C:\Users\jarms\repos\skillmatch-mcp\data")
RANKED_FILE = DATA_DIR / "ranked_jobs.md"
LAST_EMAIL_FILE = DATA_DIR / "last_email_timestamp.txt"
EMAIL_TO = "jarmstrong158@gmail.com"
EMAIL_CAP = 15


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


def cap_ranked_content(body, cap=EMAIL_CAP):
    """Trim ranked_jobs.md content to only the top N entries.
    Entries are separated by numbered headers like '1.', '2.', etc."""
    # Split on lines that start with a number followed by a period
    entries = re.split(r'\n(?=\d+\.\s)', body)

    if len(entries) <= 1:
        # No numbered entries found, or just one block — send as-is
        return body

    # First chunk is the header/preamble before entry 1
    header = entries[0]
    ranked_entries = entries[1:]

    if len(ranked_entries) <= cap:
        return body

    capped = header + "\n".join(ranked_entries[:cap])
    capped += f"\n\n({len(ranked_entries) - cap} more ranked listings not shown in email)"
    return capped


def send_email(subject, body):
    user = os.environ.get("GMAIL_USER", "")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        print("Email skipped -- credentials not configured")
        return False
    try:
        msg = MIMEText(body, "plain")
        msg["From"] = user
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
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
    body = cap_ranked_content(body, EMAIL_CAP)

    today = datetime.now().strftime("%m/%d/%Y")
    subject = f"Job Scout Rankings -- {today}"

    if send_email(subject, body):
        LAST_EMAIL_FILE.write_text(str(RANKED_FILE.stat().st_mtime))
        print("Done.")
    else:
        print("Failed to send email.")
        exit(1)
