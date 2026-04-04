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
9. Where is your resume file? (absolute path, supports .txt, .md, .docx)

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
**Does NOT perform analysis.** Accepts a job description string. Internally fetches the user's GitHub portfolio and resume, then returns all three (job description, portfolio, resume) bundled together in a structured format. Use the returned data to reason about the fit: identify matching skills, gaps, talking points, and dealbreaker conflicts. Give the user a clear recommendation.

### log_application
Logs a job application to the SQLite database at `data/applications.db`. Creates the database automatically on first use. Required fields: company, role. Optional: salary, url, status (default "applied"), notes. Call this after the user decides to apply somewhere.

### get_applications
Returns all tracked applications ordered by most recent first. Use this when the user asks about their application history or wants a status overview.

## When to Call Tools Automatically

- **Start of conversation**: Call `get_profile` to check if onboarding is needed.
- **User asks "find me jobs" or similar**: Call `search_jobs`, then use the result with web search.
- **User pastes a job description**: Call `analyze_fit` to gather data, then provide your analysis.
- **User says they applied somewhere**: Call `log_application` to track it.
- **User asks "what have I applied to"**: Call `get_applications`.

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
