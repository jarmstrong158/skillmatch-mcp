"""
Conductor worker: Reads scouted_jobs.json, emails only NEW (unranked) listings.
Caps email at top 15. Marks emailed jobs as ranked after sending.
"""
import os
import smtplib
import json
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(r"C:\Users\jarms\repos\skillmatch-mcp\data")
SCOUTED_FILE = DATA_DIR / "scouted_jobs.json"
RANKED_FILE = DATA_DIR / "ranked_jobs.md"
EMAIL_TO = "jarmstrong158@gmail.com"
EMAIL_CAP = 15


def load_scouted():
    if not SCOUTED_FILE.exists():
        return []
    try:
        with open(SCOUTED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, Exception):
        return []


def save_scouted(jobs):
    with open(SCOUTED_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)


def format_job(job, idx):
    lines = [f"{idx}. {job.get('role', 'Unknown')} @ {job.get('company', 'Unknown')}"]
    if job.get("salary"):
        lines.append(f"   Salary: {job['salary']}")
    loc = job.get("location", "")
    remote = job.get("remote", False)
    if remote:
        lines.append(f"   Location: Remote{f' ({loc})' if loc else ''}")
    elif loc:
        lines.append(f"   Location: {loc}")
    if job.get("url"):
        lines.append(f"   Link: {job['url']}")
    if job.get("source"):
        lines.append(f"   Source: {job['source']}")
    return "\n".join(lines)


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
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pw)
            s.sendmail(user, EMAIL_TO, msg.as_string())
        print(f"Email sent to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"Email failed: {e}")
        return False


if __name__ == "__main__":
    jobs = load_scouted()
    new_jobs = [j for j in jobs if not j.get("ranked", False)]

    if not new_jobs:
        print("No new (unranked) jobs to email. Skipping.")
        exit(0)

    # Cap at EMAIL_CAP most recent
    to_email = new_jobs[:EMAIL_CAP]

    # Format email body
    today = datetime.now().strftime("%m/%d/%Y")
    body_lines = [f"New Job Listings - {today}", f"{len(to_email)} new listings\n"]
    for i, job in enumerate(to_email, 1):
        body_lines.append(format_job(job, i))
        body_lines.append("")

    if len(new_jobs) > EMAIL_CAP:
        body_lines.append(f"({len(new_jobs) - EMAIL_CAP} more new listings not shown)")

    body = "\n".join(body_lines)

    # Also save to ranked_jobs.md for reference
    with open(RANKED_FILE, "w", encoding="utf-8") as f:
        f.write(body)

    subject = f"Job Scout: {len(to_email)} New Listings - {today}"
    if send_email(subject, body):
        # Mark emailed jobs as ranked
        for j in jobs:
            if not j.get("ranked", False):
                j["ranked"] = True
        save_scouted(jobs)
        print(f"Done. Emailed {len(to_email)}, marked {len(new_jobs)} as ranked.")
    else:
        print("Failed to send email.")
        exit(1)
