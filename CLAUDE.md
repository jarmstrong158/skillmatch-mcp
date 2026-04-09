# SkillMatch MCP Server

SkillMatch is a job search assistant for people who demonstrate their skills through actual work (GitHub repos, project portfolios) rather than credentials alone. It helps Claude analyze job fit, track applications, and build targeted search queries.

## Onboarding Flow

The first time a user interacts with SkillMatch, there will be no profile. When `get_profile` returns the "no profile" error, walk the user through onboarding by asking the setup questions conversationally, then call `setup` with all their answers at once.

Questions to ask during onboarding:
1. What is your name?
2. What is your current role or situation?
3. What roles are you targeting? (collect as a list)
4. What is your minimum acceptable salary? (integer)
5. Are you only looking for remote positions?
6. What is your location?
7. What are your dealbreakers? (collect as a list)
8. What is your GitHub profile URL?
9. Where is your resume file? (absolute path, supports .txt, .md, .docx — OR paste resume text directly)
10. (Optional) What is your LinkedIn profile URL?

## Tools

### setup
Saves the user's profile to `data/profile.json`. If a profile already exists, the tool returns the existing profile and requires `confirm_overwrite: true` to replace it. Always ask the user before overwriting.

### get_profile
Returns the saved profile. Call this at the start of a conversation to check if onboarding is needed. If no profile exists, trigger the onboarding flow.

### get_portfolio
Fetches the user's public GitHub repos via the GitHub API (no auth needed). Returns repo name, description, language, topics, last updated, and homepage. Skips forks. Sorted by last updated. Use this when the user asks about their portfolio or when analyzing job fit.

### get_resume
Reads the resume file from the path stored in the profile. Returns raw text content. Supports .txt, .md, and .docx formats. Use this when the user asks about their resume or when analyzing job fit.

### search_jobs
**Does NOT search the web.** Accepts a query string and combines it with the user's profile (target roles, salary floor, remote preference, location) to build an optimized search query. Returns the query string and filtering instructions. After calling this tool, use the returned query with a web search tool to find actual listings.

### analyze_fit
**Two-step process.** Accepts a job description string. First parses the JD into structured signal (hard requirements, nice-to-haves, red flags, compensation signals, role type) via the Claude API. Then fetches portfolio and selects the best resume variant for the detected role type. Returns everything bundled. Use `parsed_jd` to distinguish hard-requirement gaps from nice-to-have gaps. Flag red flags. Weight project evidence heavily when hard requirements overlap with GitHub portfolio.

### parse_jd
Standalone JD parser. Accepts a job description and returns structured JSON: hard_requirements, nice_to_haves, responsibilities, red_flags, compensation_signals, role_type, experience_level, domain. Requires ANTHROPIC_API_KEY env var.

### log_application
Logs a job application to the SQLite database at `data/applications.db`. Creates the database automatically on first use. Required fields: company, role. Optional: salary, url, status (default "applied"), notes. Call this after the user decides to apply somewhere.

### get_applications
Returns all tracked applications ordered by most recent first. Supports optional `status` filter (applied, screening, interview, offer, rejected, ghosted).

### update_application
Updates an existing application by ID. Accepts any subset: status, notes, follow_up_due_date, response_received, outcome. Automatically updates last_activity_date. Use when the user gets a response, schedules an interview, or wants to update status.

### get_follow_ups
Returns applications where follow_up_due_date is today or earlier and status is still applied or screening. Shows what needs attention. Call proactively when the user asks about their pipeline.

### get_application_patterns
After 10+ applications, analyzes the full history to find patterns: which role types get responses, which skills resonate, recurring red flags in silent roles, and recommended search adjustments. Requires ANTHROPIC_API_KEY.

### save_scouted_job
Saves a scouted job listing to `data/scouted_jobs.json`. **Always use this tool instead of writing to the file directly.** It enforces:
- **URL validation**: Rejects search result page URLs (e.g. `indeed.com/q-*`, `builtin.com/jobs/`, `ziprecruiter.com/Jobs/`). Only accepts direct job posting links (e.g. `indeed.com/viewjob?jk=...`, `greenhouse.io/.../jobs/123`, `lever.co/.../uuid`, `ashbyhq.com/.../uuid`).
- **Deduplication**: Checks company+role (case-insensitive) against both scouted jobs and applications DB.
- **Auto-timestamping**: Adds `date_found` automatically.

Required: company, role, url. Optional: salary, location, remote (bool), source.

If the URL is rejected, search Google for `"Company Name" "Role Title" site:indeed.com` (or the relevant job board) to find the direct link before trying again.

### get_scouted_jobs
Returns all scouted job listings from `scouted_jobs.json`. Pass `unranked_only: true` to filter to only unranked jobs.

### mark_jobs_ranked
Marks all unranked scouted jobs as ranked. Call this after generating a ranked report.

### add_resume
Adds a new resume variant to the profile's `resumes` array. Each variant has an id, label, target role_types, and either text or path. During fit analysis, the best variant is auto-selected based on the JD's detected role_type.

### list_resumes
Returns all stored resume variants with their labels and target role types.

### update_profile
Merges new or changed fields into the existing profile without requiring a full re-setup. Accepts any subset of profile fields. Use this when the user wants to add unlisted_skills, update dealbreaker_detail, change their salary floor, or modify any other field incrementally.

## Extended Profile Fields (all optional)

These fields give Claude richer context for fit analysis. They can be set during `setup` or added later with `update_profile`.

- **work_style**: Object with `async_preferred` (bool), `ic_vs_leadership` (ic/leadership/both), `client_facing_tolerance` (none/occasional/fine), `team_size_preference` (string).
- **optimizing_for**: Array of priorities like comp, growth, stability, remote, interesting_problems, autonomy.
- **unlisted_skills**: Skills the candidate has but aren't on their resume (e.g. MCP protocol implementation, SimPy simulation).
- **developing_skills**: Skills actively being learned -- signals trajectory, not current mastery.
- **dealbreaker_detail**: Array of objects with `dealbreaker` (string), `hardness` (absolute/strong_preference/negotiable), `notes` (string). More nuanced than the flat `dealbreakers` list.
- **rejection_patterns**: Types of roles that looked good on paper but weren't a fit, and why.

## Fit Analysis Philosophy

When analyzing job fit, project evidence and demonstrated output can and should compensate for formal experience gaps. A candidate with 4 months of building real shipped projects (MCP servers, RL agents, CI/CD pipelines) has more signal than years of credential-only experience. Weight GitHub portfolio and project complexity heavily when the resume shows a non-traditional path.

## When to Call Tools Automatically

- **Start of conversation**: Call `get_profile` to check if onboarding is needed. Use the extended profile fields (work_style, optimizing_for, etc.) when reasoning about fit.
- **User asks "find me jobs" or similar**: Call `search_jobs`, then use the result with web search.
- **User pastes a job description**: Call `analyze_fit` to gather data, then provide your analysis.
- **User says they applied somewhere**: Call `log_application` to track it.
- **User asks "what have I applied to"**: Call `get_applications`. Use `status` filter if they ask about specific statuses.
- **User gets a response or update**: Call `update_application` with the new status.
- **User asks about follow-ups**: Call `get_follow_ups` to show what needs attention.
- **User wants to understand patterns**: Call `get_application_patterns` (needs 10+ applications).
- **Scouting jobs**: Always use `save_scouted_job` to save listings. NEVER write to scouted_jobs.json directly.
- **Ranking jobs**: Call `get_scouted_jobs` with `unranked_only: true`, rank them, then call `mark_jobs_ranked`.

## Conversation Flow Examples

### First time user
1. User: "Help me find a job"
2. Call `get_profile` -> no profile found
3. Walk through onboarding questions
4. Call `setup` with answers
5. Call `search_jobs` with their target
6. Use web search with the returned query
7. Present results filtered against dealbreakers

### Analyzing a specific job
1. User pastes a job listing
2. Call `analyze_fit` with the job description
3. Review the returned portfolio, resume, and profile data
4. Identify skill matches, gaps, and relevant projects
5. Check salary floor and dealbreakers
6. Give a fit recommendation with specific talking points
7. If the user wants to apply, call `log_application`

### Checking application status
1. User: "What jobs have I applied to?"
2. Call `get_applications`
3. Present the list with dates and statuses
