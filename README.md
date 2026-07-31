<!-- mcp-name: io.github.jarmstrong158/skillmatch-mcp -->

# SkillMatch MCP

Claude-powered job fit analyzer for people who prove their skills through work, not credentials.

## What It Does

SkillMatch is an MCP server that gives Claude access to your GitHub portfolio, resume, and job search preferences. It lets Claude:

- Analyze how well you fit a specific job description based on your actual projects and resume
- Build optimized job search queries tailored to your targets and constraints
- Track every application you submit in a local SQLite database
- Pull your public GitHub repos to highlight relevant work during fit analysis

All data stays local on your machine. Nothing is sent anywhere except public GitHub API calls to fetch your own repos.

## Setup

### 1. Install the dependency

```bash
cd skillmatch-mcp
pip install -r requirements.txt
```

Speaks **MCP protocol revision `2026-07-28`** over stdio, on SDK `mcp>=2.0,<3`. It is stateless:
no `initialize` handshake and no session id, with every request carrying its own protocol version
and client identity. `tools/list` and `server/discover` return `ttlMs`/`cacheScope` freshness
hints, and tool order is stable across calls so client-side and prompt caches keep hitting.

> **Upgrading from 1.x:** 2.0.0 drops the `initialize` handshake, so it requires a client that
> speaks `2026-07-28`. Tool names, schemas, and results are unchanged — only the transport moved.
> Stay on 1.x if you need to serve an older client.

### 2. Add to Claude

**Claude Code (CLI):**

```bash
claude mcp add --scope user skillmatch -- python /path/to/skillmatch-mcp/server.py
```

Then restart Claude Code.

**Claude Desktop:**

Open your Claude Desktop configuration file and add SkillMatch to the `mcpServers` section:

```json
{
  "mcpServers": {
    "skillmatch": {
      "command": "python",
      "args": ["/path/to/skillmatch-mcp/server.py"]
    }
  }
}
```

Replace `/path/to/skillmatch-mcp/server.py` with the actual path to `server.py` on your machine, then restart Claude Desktop.

### 3. Start using it

After restarting, SkillMatch tools will be available in your conversation. Works in Claude Code and Claude Desktop.

## Onboarding

The first time you use SkillMatch, Claude will walk you through a setup conversation. It looks something like this:

```
You: Help me find a new job.

Claude: I don't have your profile set up yet. Let me ask you a few questions first.

  What is your name?
You: Jordan

Claude: What is your current role?
You: Senior frontend developer

Claude: What roles are you targeting?
You: Staff frontend engineer, senior full-stack engineer

Claude: What is your minimum acceptable salary?
You: 180000

Claude: Are you only looking for remote positions?
You: Yes

Claude: Where are you located?
You: Portland, OR

Claude: Any dealbreakers?
You: No on-call rotations, no defense contractors

Claude: What is your GitHub URL?
You: https://github.com/jordan-dev

Claude: Where is your resume file?
You: C:/Users/jordan/Documents/resume.md

Claude: Got it. Your profile is saved. Let me search for jobs matching your targets.
```

After onboarding, your profile is saved locally and reused in every future conversation.

### Extended Profile (optional)

You can enrich your profile at any time by asking Claude to update it. These fields help Claude reason about nuanced fit:

| Field | Type | Example |
|---|---|---|
| `work_style` | object | `{async_preferred: true, ic_vs_leadership: "ic", client_facing_tolerance: "none", team_size_preference: "small"}` |
| `optimizing_for` | array | `["comp", "autonomy", "interesting_problems"]` |
| `unlisted_skills` | array | `["MCP protocol implementation", "Windows installer packaging"]` |
| `developing_skills` | array | `["TypeScript", "RAG pipelines"]` |
| `dealbreaker_detail` | array | `[{dealbreaker: "on-call", hardness: "absolute"}, {dealbreaker: "relocation", hardness: "strong_preference"}]` |
| `rejection_patterns` | array | `["roles that sounded like automation but were actually IT support"]` |

These can be set during initial setup or added later with the `update_profile` tool.

## How It Works

**search_jobs** builds a search query from your profile and any keywords you provide. Claude then uses that query with its web search capabilities to find real listings. The tool itself does not search the web.

**analyze_fit** runs a two-step process. First it parses the job description into structured signal (hard requirements, nice-to-haves, red flags, compensation signals, role type) via the Claude API. Then it fetches your portfolio and auto-selects the best resume variant for the detected role type. Claude sees structured signal before raw marketing copy.

**parse_jd** is the standalone JD parser. Use it independently to pre-process a job description without running the full fit analysis.

**log_application**, **get_applications**, and **update_application** form a job search CRM. Track status (applied, screening, interview, offer, rejected, ghosted), set follow-up dates, and record outcomes.

**get_follow_ups** shows applications that need attention — where the follow-up date has passed and you're still waiting.

**get_application_patterns** analyzes your full application history (10+ needed) to find which role types get responses, which skills resonate, and recommends search adjustments.

**email_ranked_jobs.py** is a standalone Conductor worker script that emails the latest ranked job report. See [Email Worker Setup](#email-worker-setup) below for configuration.

**save_scouted_job** saves a job listing found during scouting. It validates the URL to reject search result pages and deduplicates against existing scouted jobs and applications.

**get_scouted_jobs** returns all scouted listings, optionally filtered to only unranked ones. **mark_jobs_ranked** marks all unranked jobs as ranked after a ranking report is generated.

**add_resume** and **list_resumes** manage multiple resume variants. Each variant targets specific role types (e.g. "AI Engineering" targets `ai_engineering` and `ml_engineering`). During fit analysis, the best variant is auto-selected based on the JD's detected role type.

**update_profile** merges new or changed fields into your existing profile without re-running setup.

**get_portfolio** and **get_resume** can be called independently if you want Claude to review just your repos or just your resume.

## Quick Start (No File Paths)

For the simplest setup, paste your resume directly — no local files needed:

```
You: Help me find a job.
Claude: What is your name?
You: Alex
Claude: What roles are you targeting?
You: AI engineer, ML engineer
Claude: Paste your resume or provide a file path.
You: [paste resume text here]
Claude: Profile saved. Let me search for jobs.
```

## Email Worker Setup

`email_ranked_jobs.py` sends the ranked job report via Gmail. It is designed to run as a [Conductor](https://github.com/jarmstrong158/conductor-mcp) worker on a schedule.

**Credentials live in a gitignored config file — never in the source.** Copy the template and fill it in:

```bash
cp data/email_config.example.json data/email_config.json
```

Then edit `data/email_config.json`:

```json
{
  "gmail_user": "you@gmail.com",
  "gmail_app_password": "your-16-char-app-password",
  "email_to": "you@gmail.com",
  "email_cap": 15
}
```

| Key | Required | Default |
|---|---|---|
| `gmail_user` | yes | — |
| `gmail_app_password` | yes | — |
| `email_to` | no | falls back to `gmail_user` |
| `email_cap` | no | `15` |

The whole `data/` folder is gitignored (only `.gitkeep` and `email_config.example.json` are tracked), so `email_config.json` can never be committed by accident.

**Why a config file and not environment variables?** Depending on how the worker process is launched — notably via Conductor on Windows — environment variables may not be inherited by the child process, causing silent send failures. The config file is read from a path relative to the script, so it works regardless of how the process was spawned. Environment variables (`GMAIL_USER`, `GMAIL_APP_PASSWORD`, `EMAIL_TO`, `EMAIL_CAP`) are still honored as a **fallback** when a key is absent from the config file, which is handy for CI or containerized runs. If neither source supplies credentials, the script exits with code `2` and prints setup instructions instead of failing silently.

To generate a Gmail App Password: Google Account → Security → 2-Step Verification → App Passwords. Create one for "Mail". This is not your normal account password. If you ever paste one into a source file by mistake, revoke it from that same screen.

The script reads `data/ranked_jobs.md`, caps the email to the top 15 ranked listings, sends it, and then **deletes** `ranked_jobs.md` so stale rankings are not recycled on the next run.

## File Structure

```
skillmatch-mcp/
  server.py              # MCP server (stdio JSON-RPC)
  requirements.txt       # python-docx dependency
  CLAUDE.md              # Instructions for Claude
  README.md              # This file
  email_ranked_jobs.py   # Conductor worker: emails ranked job reports
  cowork_monitor.py      # Conductor worker: monitors Cowork VM, auto-recovers
  cowork_tab.png         # Reference image for Cowork tab UI automation
  data/                  # Entire folder gitignored except the two tracked files below
    .gitkeep                  # Keeps the folder in git (tracked)
    email_config.example.json # Credential template (tracked, no real secrets)
    email_config.json         # Your real Gmail credentials (gitignored)
    profile.json              # Created on first setup (gitignored)
    applications.db           # Created on first log (gitignored)
    scouted_jobs.json         # Scouted listings (gitignored)
    ranked_jobs.md            # Latest ranked report (gitignored)
```
