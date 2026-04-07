#!/usr/bin/env python3
"""SkillMatch MCP Server - Job fit analyzer powered by Claude."""

import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
PROFILE_PATH = os.path.join(DATA_DIR, "profile.json")
DB_PATH = os.path.join(DATA_DIR, "applications.db")
SCOUTED_PATH = os.path.join(DATA_DIR, "scouted_jobs.json")

# URL patterns that are search result pages, NOT direct job postings
BAD_URL_PATTERNS = [
    r"indeed\.com/q-",            # indeed search results
    r"indeed\.com/jobs\?",        # indeed search with params
    r"indeed\.com/jobs$",         # indeed jobs landing
    r"ziprecruiter\.com/Jobs/",   # ziprecruiter search aggregator
    r"glassdoor\.com/Job/.*jobs", # glassdoor search results
    r"builtin\.com/jobs/",        # builtin listing pages
    r"linkedin\.com/jobs/search", # linkedin search
    r"linkedin\.com/jobs$",       # linkedin jobs landing
    r"remoterocketship\.com/jobs/",  # aggregator search pages
    r"jobleads\.com/us/job/",     # jobleads aggregator
    r"naukri\.com/",              # non-US job board
    r"jobstreet\.com/",           # non-US job board
    r"jooble\.org/",              # non-US job board
    r"seek\.com\.au/",            # non-US job board
]

# URL patterns that are valid direct job postings
GOOD_URL_PATTERNS = [
    r"indeed\.com/viewjob\?jk=",
    r"indeed\.com/cmp/.+",
    r"greenhouse\.io/.+/jobs/\d+",
    r"lever\.co/.+/[a-f0-9-]+",
    r"ashbyhq\.com/.+/[a-f0-9-]+",
    r"wellfound\.com/jobs/\d+",
    r"linkedin\.com/jobs/view/\S+\d+",
    r"remoterocketship\.com/us/company/.+/jobs/.+",
]

TOOLS = [
    {
        "name": "setup",
        "description": (
            "Onboard the user by collecting their job search profile. "
            "Captures: name, current_role, target_roles (list), salary_floor (integer), "
            "remote_only (bool), location, dealbreakers (list), github_url, resume_path. "
            "If a profile already exists, the 'confirm_overwrite' parameter must be true to replace it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name"},
                "current_role": {"type": "string", "description": "Current job title or situation"},
                "target_roles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of roles the user is targeting",
                },
                "salary_floor": {"type": "integer", "description": "Minimum acceptable salary (integer)"},
                "remote_only": {"type": "boolean", "description": "Whether the user only wants remote positions"},
                "location": {"type": "string", "description": "Current location or preferred location"},
                "dealbreakers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of dealbreakers (things the user will not accept)",
                },
                "github_url": {
                    "type": "string",
                    "description": "GitHub profile URL (e.g. https://github.com/username)",
                },
                "resume_path": {
                    "type": "string",
                    "description": "Absolute path to resume file (.txt, .md, or .docx)",
                },
                "confirm_overwrite": {
                    "type": "boolean",
                    "description": "Must be true to overwrite an existing profile",
                    "default": False,
                },
            },
            "required": [
                "name",
                "current_role",
                "target_roles",
                "salary_floor",
                "remote_only",
                "location",
                "dealbreakers",
                "github_url",
                "resume_path",
            ],
        },
    },
    {
        "name": "get_profile",
        "description": "Read and return the saved user profile from data/profile.json.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_portfolio",
        "description": (
            "Fetch public GitHub repos for the user. Reads github_url from the saved profile, "
            "hits the GitHub public API, and returns repo name, description, language, topics, "
            "last updated, and homepage for each repo. Sorted by last updated descending."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_resume",
        "description": (
            "Read the user's resume file. Reads resume_path from the saved profile and returns "
            "the raw text content. Supports .txt, .md, and .docx files."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_jobs",
        "description": (
            "Build an optimized job search query. Accepts a free-text query and combines it with "
            "the user's saved profile (target roles, salary floor, remote preference) to construct "
            "a search query string. Returns the query for Claude to use with web search tools. "
            "Does NOT perform the search itself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text search query or keywords to include",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "analyze_fit",
        "description": (
            "Gather all user data needed for job fit analysis. Accepts a job description, then "
            "internally fetches the user's portfolio (GitHub repos) and resume. Returns all three "
            "together in a structured bundle so Claude can reason about the fit. Does NOT perform "
            "the analysis itself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_description": {
                    "type": "string",
                    "description": "The full job description text to analyze against",
                },
            },
            "required": ["job_description"],
        },
    },
    {
        "name": "log_application",
        "description": (
            "Log a job application to the tracking database. Creates the database if it does not exist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Company name"},
                "role": {"type": "string", "description": "Role title"},
                "salary": {"type": "string", "description": "Salary or compensation info (optional)"},
                "url": {"type": "string", "description": "Job listing URL (optional)"},
                "status": {
                    "type": "string",
                    "description": "Application status",
                    "default": "applied",
                },
                "notes": {"type": "string", "description": "Any notes about the application (optional)"},
            },
            "required": ["company", "role"],
        },
    },
    {
        "name": "get_applications",
        "description": "Return all tracked job applications, ordered by most recent first.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_scouted_job",
        "description": (
            "Save a scouted job listing to the tracked scouted_jobs.json file. "
            "Validates the URL to ensure it is a DIRECT link to a specific job posting "
            "(e.g. indeed.com/viewjob?jk=..., greenhouse.io/.../jobs/...) and rejects "
            "search result page URLs (e.g. indeed.com/q-..., builtin.com/jobs/...). "
            "Also deduplicates against existing scouted jobs and applications. "
            "Returns {saved: true/false, reason: '...'} with clear feedback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Company name"},
                "role": {"type": "string", "description": "Job title / role name"},
                "url": {
                    "type": "string",
                    "description": (
                        "Direct URL to the specific job posting. Must be a direct link, "
                        "NOT a search results page. For Indeed use indeed.com/viewjob?jk=JOBKEY "
                        "or indeed.com/cmp/COMPANY. For Greenhouse use job-boards.greenhouse.io/company/jobs/ID."
                    ),
                },
                "salary": {"type": "string", "description": "Salary or compensation range (optional)"},
                "location": {"type": "string", "description": "Job location"},
                "remote": {"type": "boolean", "description": "Whether the position is remote"},
                "source": {"type": "string", "description": "Where the listing was found (e.g. indeed.com, greenhouse.io)"},
            },
            "required": ["company", "role", "url"],
        },
    },
    {
        "name": "get_scouted_jobs",
        "description": (
            "Return all scouted job listings from scouted_jobs.json. "
            "Optionally filter to only unranked jobs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "unranked_only": {
                    "type": "boolean",
                    "description": "If true, only return jobs where ranked is false",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "mark_jobs_ranked",
        "description": "Mark all unranked scouted jobs as ranked. Returns the count of jobs marked.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def read_profile():
    if not os.path.exists(PROFILE_PATH):
        return None
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_github_username(url):
    url = url.rstrip("/")
    parts = url.split("/")
    for i, part in enumerate(parts):
        if part in ("github.com",) and i + 1 < len(parts):
            return parts[i + 1]
    return url.split("/")[-1]


def read_resume_file(path):
    if not os.path.exists(path):
        return None, f"Resume file not found: {path}"

    ext = os.path.splitext(path)[1].lower()

    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), None

    if ext == ".docx":
        try:
            from docx import Document

            doc = Document(path)
            text = "\n".join(para.text for para in doc.paragraphs)
            return text, None
        except ImportError:
            return None, "python-docx is not installed. Run: pip install python-docx"
        except Exception as e:
            return None, f"Error reading .docx file: {e}"

    return None, f"Unsupported file type: {ext}. Supported: .txt, .md, .docx"


def fetch_github_repos(username):
    url = f"https://api.github.com/users/{username}/repos?per_page=100&sort=updated&direction=desc"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "SkillMatch-MCP"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, f"GitHub API error: {e.code} {e.reason}"
    except Exception as e:
        return None, f"Failed to reach GitHub API: {e}"

    repos = []
    for r in data:
        if r.get("fork"):
            continue
        repos.append({
            "name": r.get("name"),
            "description": r.get("description"),
            "language": r.get("language"),
            "topics": r.get("topics", []),
            "last_updated": r.get("updated_at"),
            "homepage": r.get("homepage"),
        })
    return repos, None


def init_db():
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            salary TEXT,
            url TEXT,
            status TEXT DEFAULT 'applied',
            notes TEXT,
            applied_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


# --- Tool handlers ---


def handle_setup(params):
    ensure_data_dir()
    if os.path.exists(PROFILE_PATH) and not params.get("confirm_overwrite", False):
        return {
            "exists": True,
            "message": (
                "A profile already exists. To overwrite it, call setup again with confirm_overwrite set to true. "
                "Ask the user to confirm before overwriting."
            ),
            "current_profile": read_profile(),
        }

    profile = {
        "name": params["name"],
        "current_role": params["current_role"],
        "target_roles": params["target_roles"],
        "salary_floor": params["salary_floor"],
        "remote_only": params["remote_only"],
        "location": params["location"],
        "dealbreakers": params["dealbreakers"],
        "github_url": params["github_url"],
        "resume_path": params["resume_path"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    return {"success": True, "message": "Profile saved.", "profile": profile}


def handle_get_profile(_params):
    profile = read_profile()
    if profile is None:
        return {
            "error": "No profile found. Run the setup tool first to create one.",
            "hint": "Ask the user onboarding questions, then call setup with their answers.",
        }
    return profile


def handle_get_portfolio(_params):
    profile = read_profile()
    if profile is None:
        return {"error": "No profile found. Run setup first."}

    github_url = profile.get("github_url", "")
    if not github_url:
        return {"error": "No github_url in profile. Run setup again to add it."}

    username = extract_github_username(github_url)
    repos, err = fetch_github_repos(username)
    if err:
        return {"error": err}

    return {"username": username, "repo_count": len(repos), "repos": repos}


def handle_get_resume(_params):
    profile = read_profile()
    if profile is None:
        return {"error": "No profile found. Run setup first."}

    resume_path = profile.get("resume_path", "")
    if not resume_path:
        return {"error": "No resume_path in profile. Run setup again to add it."}

    content, err = read_resume_file(resume_path)
    if err:
        return {"error": err}

    return {"file": resume_path, "content": content}


def handle_search_jobs(params):
    profile = read_profile()
    query = params.get("query", "")

    parts = [query]

    if profile:
        if profile.get("target_roles"):
            roles = " OR ".join(f'"{r}"' for r in profile["target_roles"])
            parts.append(f"({roles})")
        if profile.get("remote_only"):
            parts.append("remote")
        if profile.get("location") and not profile.get("remote_only"):
            parts.append(profile["location"])
        if profile.get("salary_floor"):
            parts.append(f"${profile['salary_floor']}+")

        salary_floor = profile.get("salary_floor")
        dealbreakers = profile.get("dealbreakers", [])
    else:
        salary_floor = None
        dealbreakers = []

    search_query = " ".join(p for p in parts if p)

    return {
        "search_query": search_query,
        "instructions": (
            "Use this query with a web search tool to find current job listings. "
            "Filter results against the user's dealbreakers and salary floor."
        ),
        "salary_floor": salary_floor,
        "dealbreakers": dealbreakers,
    }


def handle_analyze_fit(params):
    job_description = params.get("job_description", "")

    profile = read_profile()
    if profile is None:
        return {"error": "No profile found. Run setup first."}

    # Fetch portfolio
    github_url = profile.get("github_url", "")
    portfolio = None
    portfolio_error = None
    if github_url:
        username = extract_github_username(github_url)
        repos, err = fetch_github_repos(username)
        if err:
            portfolio_error = err
        else:
            portfolio = {"username": username, "repo_count": len(repos), "repos": repos}
    else:
        portfolio_error = "No github_url in profile."

    # Fetch resume
    resume_content = None
    resume_error = None
    resume_path = profile.get("resume_path", "")
    if resume_path:
        content, err = read_resume_file(resume_path)
        if err:
            resume_error = err
        else:
            resume_content = content
    else:
        resume_error = "No resume_path in profile."

    return {
        "instructions": (
            "Analyze the fit between this job description and the user's profile, portfolio, and resume. "
            "Identify matching skills, gaps, and talking points. Consider the user's dealbreakers and salary floor. "
            "Give a clear recommendation with reasoning."
        ),
        "job_description": job_description,
        "profile": profile,
        "portfolio": portfolio,
        "portfolio_error": portfolio_error,
        "resume": resume_content,
        "resume_error": resume_error,
    }


def handle_log_application(params):
    try:
        conn = init_db()
        applied_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO applications (company, role, salary, url, status, notes, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                params["company"],
                params["role"],
                params.get("salary"),
                params.get("url"),
                params.get("status", "applied"),
                params.get("notes"),
                applied_at,
            ),
        )
        conn.commit()
        conn.close()
        return {
            "success": True,
            "message": f"Logged application to {params['company']} for {params['role']}.",
            "applied_at": applied_at,
        }
    except Exception as e:
        return {"error": f"Failed to log application: {e}"}


def handle_get_applications(_params):
    if not os.path.exists(DB_PATH):
        return {
            "applications": [],
            "message": "No applications tracked yet. Use log_application to start tracking.",
        }

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM applications ORDER BY applied_at DESC").fetchall()
        conn.close()
        applications = [dict(row) for row in rows]
        return {"count": len(applications), "applications": applications}
    except Exception as e:
        return {"error": f"Failed to read applications: {e}"}


def _read_scouted_jobs():
    """Read scouted_jobs.json, return list (empty list if missing/empty)."""
    if not os.path.exists(SCOUTED_PATH):
        return []
    try:
        with open(SCOUTED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, Exception):
        return []


def _write_scouted_jobs(jobs):
    """Write scouted jobs list to scouted_jobs.json."""
    ensure_data_dir()
    with open(SCOUTED_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)


def _validate_job_url(url):
    """Validate that a URL is a direct job posting, not a search results page.
    Returns (is_valid, reason)."""
    if not url or not url.startswith("http"):
        return False, "URL must start with http:// or https://"

    # Check against bad patterns first
    for pattern in BAD_URL_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return False, (
                f"Rejected: URL matches search results page pattern '{pattern}'. "
                "You must provide a direct link to the specific job posting. "
                "For Indeed, use indeed.com/viewjob?jk=JOBKEY. "
                "For Greenhouse, use job-boards.greenhouse.io/company/jobs/ID. "
                "Search for the exact company + role title to find the direct link."
            )

    # Check if it matches a known good pattern (advisory, not required)
    matches_good = any(re.search(p, url, re.IGNORECASE) for p in GOOD_URL_PATTERNS)
    if not matches_good:
        # Allow it but warn — could be a valid direct link we don't have a pattern for
        return True, "URL accepted (not a known job board pattern — verify it links to a specific posting)"

    return True, "URL validated"


def _is_duplicate_scouted(company, role, scouted_jobs):
    """Check if company+role already exists in scouted jobs (case-insensitive)."""
    key = (company.lower().strip(), role.lower().strip())
    for j in scouted_jobs:
        existing_key = (j.get("company", "").lower().strip(), j.get("role", "").lower().strip())
        if key == existing_key:
            return True
    return False


def _is_duplicate_application(company, role):
    """Check if company+role already exists in applications DB (case-insensitive)."""
    if not os.path.exists(DB_PATH):
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT company, role FROM applications").fetchall()
        conn.close()
        key = (company.lower().strip(), role.lower().strip())
        for row in rows:
            existing_key = (row["company"].lower().strip(), row["role"].lower().strip())
            if key == existing_key:
                return True
    except Exception:
        pass
    return False


def handle_save_scouted_job(params):
    company = params.get("company", "").strip()
    role = params.get("role", "").strip()
    url = params.get("url", "").strip()

    if not company or not role or not url:
        return {"saved": False, "reason": "company, role, and url are all required"}

    # Validate URL
    is_valid, reason = _validate_job_url(url)
    if not is_valid:
        return {"saved": False, "reason": reason}

    # Check for duplicates
    scouted_jobs = _read_scouted_jobs()

    if _is_duplicate_scouted(company, role, scouted_jobs):
        return {"saved": False, "reason": f"Duplicate: '{company} — {role}' already exists in scouted jobs"}

    if _is_duplicate_application(company, role):
        return {"saved": False, "reason": f"Duplicate: '{company} — {role}' already exists in applications (already applied)"}

    # Build entry
    entry = {
        "company": company,
        "role": role,
        "url": url,
        "salary": params.get("salary"),
        "location": params.get("location", ""),
        "remote": params.get("remote", False),
        "date_found": datetime.now(timezone.utc).isoformat(),
        "source": params.get("source", ""),
        "ranked": False,
    }

    scouted_jobs.append(entry)
    _write_scouted_jobs(scouted_jobs)

    url_note = f" ({reason})" if "not a known" in reason else ""
    return {
        "saved": True,
        "reason": f"Saved: {company} — {role}{url_note}",
        "total_scouted": len(scouted_jobs),
    }


def handle_get_scouted_jobs(params):
    jobs = _read_scouted_jobs()
    unranked_only = params.get("unranked_only", False)
    if unranked_only:
        jobs = [j for j in jobs if not j.get("ranked", False)]
    return {"count": len(jobs), "jobs": jobs}


def handle_mark_jobs_ranked(_params):
    jobs = _read_scouted_jobs()
    count = 0
    for j in jobs:
        if not j.get("ranked", False):
            j["ranked"] = True
            count += 1
    _write_scouted_jobs(jobs)
    return {"marked": count, "total": len(jobs)}


HANDLERS = {
    "setup": handle_setup,
    "get_profile": handle_get_profile,
    "get_portfolio": handle_get_portfolio,
    "get_resume": handle_get_resume,
    "search_jobs": handle_search_jobs,
    "analyze_fit": handle_analyze_fit,
    "log_application": handle_log_application,
    "get_applications": handle_get_applications,
    "save_scouted_job": handle_save_scouted_job,
    "get_scouted_jobs": handle_get_scouted_jobs,
    "mark_jobs_ranked": handle_mark_jobs_ranked,
}


# --- JSON-RPC stdio transport (line-delimited JSON) ---


def main():
    ensure_data_dir()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "skillmatch-mcp", "version": "1.0.0"},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            }
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            handler = HANDLERS.get(tool_name)

            if handler is None:
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"})}],
                        "isError": True,
                    },
                }
            else:
                try:
                    result = handler(tool_args)
                except Exception as e:
                    result = {"error": f"Tool '{tool_name}' failed: {e}"}
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    },
                }
        elif method.startswith("notifications/"):
            continue
        else:
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
