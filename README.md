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

## How It Works

**search_jobs** builds a search query from your profile and any keywords you provide. Claude then uses that query with its web search capabilities to find real listings. The tool itself does not search the web.

**analyze_fit** bundles your GitHub portfolio, resume text, and the job description together. Claude then reasons about the fit, identifying skill matches, gaps, and talking points. The tool gathers the data; Claude does the thinking.

**log_application** and **get_applications** maintain a local SQLite database so you can track where you have applied, when, and any notes about the process.

**save_scouted_job** saves a job listing found during scouting. It validates the URL to reject search result pages (e.g. `indeed.com/q-*`) and only accepts direct posting links (e.g. `indeed.com/viewjob?jk=...`). It also deduplicates against existing scouted jobs and the applications database.

**get_scouted_jobs** returns all scouted listings, optionally filtered to only unranked ones. **mark_jobs_ranked** marks all unranked jobs as ranked after a ranking report is generated.

**get_portfolio** and **get_resume** can be called independently if you want Claude to review just your repos or just your resume.

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
  data/
    .gitkeep             # Keeps the folder in git
    profile.json         # Created on first setup (gitignored)
    applications.db      # Created on first log (gitignored)
    scouted_jobs.json    # Scouted listings (gitignored)
    ranked_jobs.md       # Latest ranked report (gitignored)
```
