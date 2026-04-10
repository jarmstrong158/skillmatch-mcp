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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
                    "description": "Absolute path to resume file (.txt, .md, or .docx). Optional if resume_text is provided.",
                },
                "resume_text": {
                    "type": "string",
                    "description": "Resume content as plain text or markdown. Use this instead of resume_path for portability.",
                },
                "linkedin_url": {
                    "type": "string",
                    "description": "Public LinkedIn profile URL for supplemental work history (optional)",
                },
                "work_style": {
                    "type": "object",
                    "description": "Work style preferences",
                    "properties": {
                        "async_preferred": {"type": "boolean"},
                        "ic_vs_leadership": {"type": "string", "enum": ["ic", "leadership", "both"]},
                        "client_facing_tolerance": {"type": "string", "enum": ["none", "occasional", "fine"]},
                        "team_size_preference": {"type": "string"},
                    },
                },
                "optimizing_for": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What the candidate prioritizes: comp, growth, stability, remote, interesting_problems, autonomy",
                },
                "unlisted_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Skills the candidate has but aren't formalized on resume",
                },
                "developing_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Skills actively being learned -- signals trajectory",
                },
                "dealbreaker_detail": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "dealbreaker": {"type": "string"},
                            "hardness": {"type": "string", "enum": ["absolute", "strong_preference", "negotiable"]},
                            "notes": {"type": "string"},
                        },
                    },
                    "description": "Detailed dealbreakers with hardness levels and notes",
                },
                "rejection_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Types of roles that looked good but weren't, and brief reason why",
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
        "description": "Return all tracked job applications, ordered by most recent first. Optionally filter by status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status: applied, screening, interview, offer, rejected, ghosted (optional)",
                },
            },
        },
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
    {
        "name": "parse_jd",
        "description": (
            "Parse a job description into structured signal: hard requirements, nice-to-haves, "
            "responsibilities, red flags, compensation signals, role type, experience level, and domain. "
            "Uses Claude API internally. Returns structured JSON for better fit reasoning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_description": {
                    "type": "string",
                    "description": "The full job description text to parse",
                },
            },
            "required": ["job_description"],
        },
    },
    {
        "name": "update_application",
        "description": (
            "Update an existing application by ID. Accepts any subset of fields: status, notes, "
            "follow_up_due_date, response_received, outcome. Automatically updates last_activity_date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Application ID to update"},
                "status": {
                    "type": "string",
                    "description": "New status: applied, screening, interview, offer, rejected, ghosted",
                },
                "notes": {"type": "string"},
                "follow_up_due_date": {"type": "string", "description": "ISO date (YYYY-MM-DD) for follow-up reminder"},
                "response_received": {"type": "boolean"},
                "outcome": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_follow_ups",
        "description": (
            "Return all applications where follow_up_due_date is today or earlier and status is "
            "still applied or screening. Shows what needs attention."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_application_patterns",
        "description": (
            "After 10+ applications, analyze the full history to find patterns: which role types "
            "get responses, which skills resonate, which red flags recur in silent roles, and "
            "recommended search adjustments. Uses Claude API internally."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_resume",
        "description": (
            "Add a new resume variant to the profile. Each variant targets specific role types "
            "for automatic selection during fit analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique identifier for this variant (e.g. 'ai_eng')"},
                "label": {"type": "string", "description": "Human-readable label (e.g. 'AI Engineering')"},
                "role_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of role_type values this resume targets (e.g. ['ai_engineering', 'ml_engineering'])",
                },
                "path": {"type": "string", "description": "Absolute path to resume file (optional if text provided)"},
                "text": {"type": "string", "description": "Resume content as plain text/markdown (optional if path provided)"},
            },
            "required": ["id", "label", "role_types"],
        },
    },
    {
        "name": "list_resumes",
        "description": "Return all stored resume variants with their labels and target role types.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_profile",
        "description": (
            "Update the user's profile with new or changed fields. Accepts any subset of profile "
            "fields and merges them into the existing profile.json without requiring a full re-setup. "
            "Useful for adding unlisted_skills, updating dealbreaker_detail, or changing any field."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "current_role": {"type": "string"},
                "target_roles": {"type": "array", "items": {"type": "string"}},
                "salary_floor": {"type": "integer"},
                "remote_only": {"type": "boolean"},
                "location": {"type": "string"},
                "dealbreakers": {"type": "array", "items": {"type": "string"}},
                "github_url": {"type": "string"},
                "resume_path": {"type": "string"},
                "work_style": {
                    "type": "object",
                    "properties": {
                        "async_preferred": {"type": "boolean"},
                        "ic_vs_leadership": {"type": "string", "enum": ["ic", "leadership", "both"]},
                        "client_facing_tolerance": {"type": "string", "enum": ["none", "occasional", "fine"]},
                        "team_size_preference": {"type": "string"},
                    },
                },
                "optimizing_for": {"type": "array", "items": {"type": "string"}},
                "unlisted_skills": {"type": "array", "items": {"type": "string"}},
                "developing_skills": {"type": "array", "items": {"type": "string"}},
                "dealbreaker_detail": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "dealbreaker": {"type": "string"},
                            "hardness": {"type": "string", "enum": ["absolute", "strong_preference", "negotiable"]},
                            "notes": {"type": "string"},
                        },
                    },
                },
                "rejection_patterns": {"type": "array", "items": {"type": "string"}},
            },
        },
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


def _call_claude(prompt, max_tokens=2048):
    """Call Claude API directly. Returns response text or None on failure."""
    if not ANTHROPIC_API_KEY:
        return None, "ANTHROPIC_API_KEY not set"
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data.get("content", [{}])[0].get("text", "")
        return text, None
    except Exception as e:
        return None, f"Claude API error: {e}"


def parse_job_description(jd_text):
    """Parse a job description into structured signal via Claude API.
    Returns (parsed_dict, error_string). Falls back gracefully."""
    prompt = (
        "Parse this job description into structured JSON. Return ONLY valid JSON, no preamble or markdown.\n\n"
        "Required schema:\n"
        '{\n'
        '  "hard_requirements": ["list of non-negotiable requirements"],\n'
        '  "nice_to_haves": ["list of preferred but optional skills"],\n'
        '  "responsibilities": ["actual day-to-day tasks, not aspirational language"],\n'
        '  "red_flags": ["on-call, travel, clearance, relocation, contract ambiguity, vague comp"],\n'
        '  "compensation_signals": {\n'
        '    "listed_salary": "string or null",\n'
        '    "estimated_range": "string or null",\n'
        '    "equity_mentioned": false,\n'
        '    "contract_vs_fulltime": "string"\n'
        '  },\n'
        '  "role_type": "one of: ai_engineering, automation_rpa, ml_engineering, data_engineering, ops_adjacent, other",\n'
        '  "experience_level": "one of: entry, mid, senior, staff, unknown",\n'
        '  "domain": "industry or domain the company operates in"\n'
        '}\n\n'
        "Job description:\n" + jd_text
    )
    text, err = _call_claude(prompt, max_tokens=2048)
    if err:
        return None, err
    # Extract JSON from response (handle potential markdown fencing)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"Failed to parse Claude response as JSON: {e}"


def _resolve_resume(profile):
    """Resolve resume content from profile. Priority: resume_text > resume_path.
    Returns (content, source_label, error)."""
    # Direct text takes priority
    resume_text = profile.get("resume_text")
    if resume_text:
        return resume_text, "resume_text", None

    # File path fallback
    resume_path = profile.get("resume_path", "")
    if resume_path:
        content, err = read_resume_file(resume_path)
        if err:
            return None, "resume_path", err
        return content, "resume_path", None

    return None, None, "No resume source in profile (set resume_text or resume_path)."


def _select_resume_for_role(profile, role_type):
    """Select the best resume variant for a given role_type.
    Returns (content, label, source, error)."""
    resumes = profile.get("resumes", [])
    if not resumes:
        # Fall back to single resume
        content, source, err = _resolve_resume(profile)
        return content, "default", source, err

    # Find matching variant by role_type
    for r in resumes:
        if role_type and role_type in r.get("role_types", []):
            text = r.get("text")
            if text:
                return text, r.get("label", "matched"), "resume_text", None
            path = r.get("path")
            if path:
                content, err = read_resume_file(path)
                return content, r.get("label", "matched"), "resume_path", err

    # No match — use first variant or default
    first = resumes[0]
    text = first.get("text")
    if text:
        return text, first.get("label", "default"), "resume_text", None
    path = first.get("path")
    if path:
        content, err = read_resume_file(path)
        return content, first.get("label", "default"), "resume_path", err

    # Fall back to single resume
    content, source, err = _resolve_resume(profile)
    return content, "default", source, err


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
            applied_at TEXT NOT NULL,
            last_activity_date TEXT,
            follow_up_due_date TEXT,
            response_received INTEGER DEFAULT 0,
            outcome TEXT
        )"""
    )
    conn.commit()
    # Migrate existing tables — add columns if missing
    cursor = conn.execute("PRAGMA table_info(applications)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    migrations = {
        "last_activity_date": "TEXT",
        "follow_up_due_date": "TEXT",
        "response_received": "INTEGER DEFAULT 0",
        "outcome": "TEXT",
    }
    for col, col_type in migrations.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {col_type}")
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Optional fields
    for key in ("resume_path", "resume_text", "linkedin_url",
                "work_style", "optimizing_for", "unlisted_skills",
                "developing_skills", "dealbreaker_detail", "rejection_patterns"):
        if key in params:
            profile[key] = params[key]

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

    content, source, err = _resolve_resume(profile)
    if err:
        return {"error": err}

    return {"source": source, "content": content}


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


def _build_profile_context(profile):
    """Format extended profile fields into a natural language summary."""
    lines = []

    ws = profile.get("work_style")
    if ws:
        parts = []
        if ws.get("async_preferred"):
            parts.append("prefers async")
        ic = ws.get("ic_vs_leadership")
        if ic:
            parts.append(f"{ic}-focused")
        cft = ws.get("client_facing_tolerance")
        if cft:
            parts.append(f"client-facing: {cft}")
        tsp = ws.get("team_size_preference")
        if tsp:
            parts.append(f"{tsp} teams")
        if parts:
            lines.append(f"Work style: {', '.join(parts)}")

    opt = profile.get("optimizing_for")
    if opt:
        lines.append(f"Optimizing for: {', '.join(opt)}")

    unlisted = profile.get("unlisted_skills")
    if unlisted:
        lines.append(f"Unlisted skills: {', '.join(unlisted)}")

    developing = profile.get("developing_skills")
    if developing:
        lines.append(f"Developing: {', '.join(developing)}")

    dbd = profile.get("dealbreaker_detail")
    if dbd:
        db_parts = []
        for d in dbd:
            name = d.get("dealbreaker", "")
            hardness = d.get("hardness", "")
            entry = f"{name} ({hardness})" if hardness else name
            db_parts.append(entry)
        if db_parts:
            lines.append(f"Dealbreakers: {', '.join(db_parts)}")

    rp = profile.get("rejection_patterns")
    if rp:
        lines.append(f"Rejection patterns: {'; '.join(rp)}")

    return "\n".join(lines) if lines else None


def handle_analyze_fit(params):
    job_description = params.get("job_description", "")

    profile = read_profile()
    if profile is None:
        return {"error": "No profile found. Run setup first."}

    # Phase 2: Parse JD into structured signal (graceful fallback)
    parsed_jd = None
    parse_error = None
    parsed_jd, parse_error = parse_job_description(job_description)

    # Determine role_type for resume selection
    role_type = parsed_jd.get("role_type") if parsed_jd else None

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

    # Phase 5: Select best resume variant for this role type
    resume_content, resume_label, resume_source, resume_error = _select_resume_for_role(profile, role_type)

    # Build rich profile context summary for Claude
    profile_context = _build_profile_context(profile)

    instructions = (
        "Analyze the fit between this job description and the user's profile, portfolio, and resume. "
    )
    if parsed_jd:
        instructions += (
            "A structured parse of the JD is provided — use it to distinguish genuine hard-requirement gaps "
            "from nice-to-have gaps. Flag any red_flags found. Use compensation_signals for salary alignment "
            "even when salary isn't explicitly listed. "
        )
    instructions += (
        "Weight project evidence heavily when hard requirements overlap with the GitHub portfolio. "
        "Project evidence and demonstrated output can and should compensate for formal experience gaps. "
        "Consider the user's dealbreakers and salary floor. Give a clear recommendation with reasoning."
    )

    result = {
        "instructions": instructions,
        "parsed_jd": parsed_jd,
        "parse_error": parse_error,
        "job_description": job_description,
        "profile": profile,
        "profile_context": profile_context,
        "portfolio": portfolio,
        "portfolio_error": portfolio_error,
        "resume": resume_content,
        "resume_label": resume_label,
        "resume_source": resume_source,
        "resume_error": resume_error,
    }
    return result


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


def handle_get_applications(params):
    if not os.path.exists(DB_PATH):
        return {
            "applications": [],
            "message": "No applications tracked yet. Use log_application to start tracking.",
        }

    try:
        conn = init_db()
        conn.row_factory = sqlite3.Row
        status_filter = params.get("status")
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM applications WHERE status = ? ORDER BY applied_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM applications ORDER BY applied_at DESC").fetchall()
        conn.close()
        applications = [dict(row) for row in rows]
        return {"count": len(applications), "applications": applications}
    except Exception as e:
        return {"error": f"Failed to read applications: {e}"}


def handle_parse_jd(params):
    jd = params.get("job_description", "")
    if not jd:
        return {"error": "job_description is required"}
    parsed, err = parse_job_description(jd)
    if err:
        return {"error": err}
    return parsed


def handle_update_application(params):
    app_id = params.get("id")
    if app_id is None:
        return {"error": "id is required"}

    try:
        conn = init_db()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if not row:
            conn.close()
            return {"error": f"No application found with id {app_id}"}

        updates = []
        values = []
        for field in ("status", "notes", "follow_up_due_date", "outcome"):
            if field in params:
                updates.append(f"{field} = ?")
                values.append(params[field])
        if "response_received" in params:
            updates.append("response_received = ?")
            values.append(1 if params["response_received"] else 0)

        if not updates:
            conn.close()
            return {"error": "No valid fields to update"}

        updates.append("last_activity_date = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(app_id)

        conn.execute(f"UPDATE applications SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()

        row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        conn.close()
        return {"success": True, "application": dict(row)}
    except Exception as e:
        return {"error": f"Failed to update application: {e}"}


def handle_get_follow_ups(_params):
    try:
        conn = init_db()
        conn.row_factory = sqlite3.Row
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT * FROM applications WHERE follow_up_due_date IS NOT NULL "
            "AND follow_up_due_date <= ? AND status IN ('applied', 'screening') "
            "ORDER BY follow_up_due_date ASC",
            (today,),
        ).fetchall()
        conn.close()

        follow_ups = []
        for row in rows:
            d = dict(row)
            applied = d.get("applied_at", "")[:10]
            if applied:
                try:
                    days = (datetime.now(timezone.utc).date() - datetime.fromisoformat(applied).date()).days
                except Exception:
                    days = None
            else:
                days = None
            d["days_since_applied"] = days
            follow_ups.append(d)

        return {"count": len(follow_ups), "follow_ups": follow_ups}
    except Exception as e:
        return {"error": f"Failed to get follow-ups: {e}"}


def handle_get_application_patterns(_params):
    try:
        conn = init_db()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM applications ORDER BY applied_at DESC").fetchall()
        conn.close()
        applications = [dict(row) for row in rows]
    except Exception as e:
        return {"error": f"Failed to read applications: {e}"}

    if len(applications) < 10:
        return {
            "error": f"Need at least 10 applications for pattern analysis. Currently have {len(applications)}.",
            "count": len(applications),
        }

    summary = json.dumps(applications, indent=2, default=str)
    prompt = (
        "Analyze this job application history and return JSON with these fields:\n"
        "- responding_role_types: which role types/titles are getting responses\n"
        "- silent_role_types: which role types get no response\n"
        "- resonating_skills: skills or background elements that seem to correlate with responses\n"
        "- recurring_red_flags: red flags that appeared in roles that didn't respond\n"
        "- recommended_adjustments: specific changes to search criteria or application strategy\n\n"
        "Return ONLY valid JSON, no preamble.\n\n"
        "Application history:\n" + summary
    )
    text, err = _call_claude(prompt, max_tokens=2048)
    if err:
        return {"error": err}
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw_analysis": text}


def handle_add_resume(params):
    profile = read_profile()
    if profile is None:
        return {"error": "No profile found. Run setup first."}

    rid = params.get("id", "").strip()
    label = params.get("label", "").strip()
    role_types = params.get("role_types", [])
    path = params.get("path")
    text = params.get("text")

    if not rid or not label or not role_types:
        return {"error": "id, label, and role_types are all required"}
    if not path and not text:
        return {"error": "Either path or text must be provided"}

    resumes = profile.get("resumes", [])

    # Check for duplicate id
    for r in resumes:
        if r.get("id") == rid:
            return {"error": f"Resume variant with id '{rid}' already exists. Use a different id."}

    entry = {"id": rid, "label": label, "role_types": role_types}
    if text:
        entry["text"] = text
    if path:
        entry["path"] = path

    resumes.append(entry)
    profile["resumes"] = resumes
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()

    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    return {"success": True, "resume_count": len(resumes), "added": entry}


def handle_list_resumes(_params):
    profile = read_profile()
    if profile is None:
        return {"error": "No profile found. Run setup first."}

    resumes = profile.get("resumes", [])
    summary = []
    for r in resumes:
        summary.append({
            "id": r.get("id"),
            "label": r.get("label"),
            "role_types": r.get("role_types", []),
            "has_text": bool(r.get("text")),
            "has_path": bool(r.get("path")),
        })

    default_source = None
    if profile.get("resume_text"):
        default_source = "resume_text"
    elif profile.get("resume_path"):
        default_source = "resume_path"

    return {
        "variants": summary,
        "variant_count": len(resumes),
        "default_resume_source": default_source,
    }


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


SCOUTED_CAP = 50


def _write_scouted_jobs(jobs):
    """Write scouted jobs list to scouted_jobs.json. Trims oldest ranked jobs beyond cap."""
    ensure_data_dir()
    if len(jobs) > SCOUTED_CAP:
        # Keep all unranked, trim oldest ranked to stay under cap
        unranked = [j for j in jobs if not j.get("ranked", False)]
        ranked = [j for j in jobs if j.get("ranked", False)]
        keep = SCOUTED_CAP - len(unranked)
        if keep > 0:
            jobs = unranked + ranked[-keep:]  # keep most recent ranked
        else:
            jobs = unranked[-SCOUTED_CAP:]  # shouldn't happen but safety
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


def handle_update_profile(params):
    profile = read_profile()
    if profile is None:
        return {"error": "No profile found. Run setup first to create one."}

    updatable = (
        "name", "current_role", "target_roles", "salary_floor", "remote_only",
        "location", "dealbreakers", "github_url", "resume_path", "resume_text",
        "linkedin_url", "work_style", "optimizing_for", "unlisted_skills",
        "developing_skills", "dealbreaker_detail", "rejection_patterns",
    )
    updated = []
    for key in updatable:
        if key in params:
            profile[key] = params[key]
            updated.append(key)

    if not updated:
        return {"error": "No valid fields provided to update."}

    profile["updated_at"] = datetime.now(timezone.utc).isoformat()

    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    return {"success": True, "updated_fields": updated, "profile": profile}


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
    "update_profile": handle_update_profile,
    "parse_jd": handle_parse_jd,
    "update_application": handle_update_application,
    "get_follow_ups": handle_get_follow_ups,
    "get_application_patterns": handle_get_application_patterns,
    "add_resume": handle_add_resume,
    "list_resumes": handle_list_resumes,
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
