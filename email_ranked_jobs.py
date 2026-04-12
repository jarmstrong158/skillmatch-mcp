"""
Conductor worker: Reads ranked_jobs.md (written by the scheduled ranker task)
and emails it. Caps the email body at the top 15 ranked entries.
"""
import re
import smtplib
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(r"C:\Users\jarms\repos\skillmatch-mcp\data")
RANKED_FILE = DATA_DIR / "ranked_jobs.md"

# Hardcode these directly — env vars are unreliable depending on how the
# worker process is launched (e.g. via Conductor on Windows).
# See README: Email Worker Setup.
GMAIL_USER = "you@gmail.com"
GMAIL_APP_PASSWORD = "xxxx xxxx xxxx xxxx"
EMAIL_TO = "you@gmail.com"
EMAIL_CAP = 15


def cap_ranked_content(body, cap=EMAIL_CAP):
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


def send_email(subject, body):
    try:
        msg = MIMEText(body, "plain")
        msg["From"] = GMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.sendmail(GMAIL_USER, EMAIL_TO, msg.as_string())
        print(f"Email sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False


if __name__ == "__main__":
    if not RANKED_FILE.exists():
        print("No ranked_jobs.md found.")
        exit(0)

    body = RANKED_FILE.read_text(encoding="utf-8")
    body = cap_ranked_content(body, EMAIL_CAP)

    today = datetime.now().strftime("%m/%d/%Y")
    subject = f"Job Scout Rankings -- {today}"

    if send_email(subject, body):
        RANKED_FILE.unlink()
        print("Done.")
    else:
        print("Failed.")
        exit(1)
