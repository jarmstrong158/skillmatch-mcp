"""
Comprehensive pytest test suite for skillmatch-mcp server.py.

Tests logic functions directly — no MCP protocol layer, no real network calls.
Uses tmp_path to isolate all file I/O.
"""

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — import server.py from parent directory
# ---------------------------------------------------------------------------
SERVER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SERVER_DIR))

import server  # noqa: E402  (side effect: sets DATA_DIR, DB_PATH, etc.)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(tmp_path, **overrides):
    """Write a minimal valid profile.json in tmp_path/data/ and return it."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    profile = {
        "name": "Test User",
        "current_role": "Software Engineer",
        "target_roles": ["Senior SWE", "Staff Engineer"],
        "salary_floor": 150000,
        "remote_only": True,
        "location": "Remote",
        "dealbreakers": ["on-call"],
        "github_url": "https://github.com/testuser",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    profile.update(overrides)
    profile_path = data_dir / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    return profile, str(profile_path)


def _patch_paths(tmp_path, monkeypatch):
    """Redirect all DATA_DIR, PROFILE_PATH, DB_PATH, SCOUTED_PATH to tmp_path."""
    data_dir = str(tmp_path / "data")
    profile_path = str(tmp_path / "data" / "profile.json")
    db_path = str(tmp_path / "data" / "applications.db")
    scouted_path = str(tmp_path / "data" / "scouted_jobs.json")

    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "PROFILE_PATH", profile_path)
    monkeypatch.setattr(server, "DB_PATH", db_path)
    monkeypatch.setattr(server, "SCOUTED_PATH", scouted_path)

    os.makedirs(data_dir, exist_ok=True)
    return data_dir, profile_path, db_path, scouted_path


def _make_mock_response(body: bytes, status: int = 200):
    """Return a mock urllib response object."""
    mock = MagicMock()
    mock.status = status
    mock.read.return_value = body
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _make_http_error(code: int):
    """Return a urllib.error.HTTPError with the given code."""
    return urllib.error.HTTPError(url="http://example.com", code=code, msg="Error", hdrs={}, fp=None)


# ===========================================================================
# 1. URL Validation (_validate_job_url)
# ===========================================================================

class TestValidateJobUrl:
    """Tests for _validate_job_url — bad URLs rejected, good URLs accepted."""

    # --- Bad patterns: search result pages ---

    def test_indeed_search_query_rejected(self):
        url = "https://www.indeed.com/q-software-engineer-jobs.html"
        valid, reason = server._validate_job_url(url)
        assert not valid
        assert "Rejected" in reason

    def test_indeed_jobs_with_params_rejected(self):
        url = "https://www.indeed.com/jobs?q=software+engineer&l=remote"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_indeed_jobs_landing_rejected(self):
        url = "https://www.indeed.com/jobs"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_linkedin_search_rejected(self):
        url = "https://www.linkedin.com/jobs/search/?keywords=engineer"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_linkedin_jobs_landing_rejected(self):
        url = "https://www.linkedin.com/jobs"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_ziprecruiter_search_rejected(self):
        url = "https://www.ziprecruiter.com/Jobs/Software-Engineer"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_glassdoor_search_rejected(self):
        url = "https://www.glassdoor.com/Job/software-engineer-jobs-SRCH_KO0,17.htm"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_builtin_listing_page_rejected(self):
        url = "https://builtin.com/jobs/software-engineer"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_remoterocketship_search_rejected(self):
        url = "https://remoterocketship.com/jobs/software-engineer"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_naukri_rejected(self):
        url = "https://www.naukri.com/software-engineer-jobs"
        valid, reason = server._validate_job_url(url)
        assert not valid

    def test_jobleads_rejected(self):
        url = "https://www.jobleads.com/us/job/software-engineer-12345"
        valid, reason = server._validate_job_url(url)
        assert not valid

    # --- Bad URL basics ---

    def test_empty_url_rejected(self):
        valid, reason = server._validate_job_url("")
        assert not valid

    def test_no_http_rejected(self):
        valid, reason = server._validate_job_url("greenhouse.io/company/jobs/123")
        assert not valid

    def test_none_url_rejected(self):
        valid, reason = server._validate_job_url(None)
        assert not valid

    # --- Good patterns: direct job postings ---

    def test_indeed_viewjob_accepted(self):
        url = "https://www.indeed.com/viewjob?jk=abc123def456"
        valid, reason = server._validate_job_url(url)
        assert valid

    def test_greenhouse_direct_accepted(self):
        url = "https://job-boards.greenhouse.io/acmecorp/jobs/4567890"
        valid, reason = server._validate_job_url(url)
        assert valid

    def test_lever_direct_accepted(self):
        url = "https://jobs.lever.co/acmecorp/1a2b3c4d-5e6f-7890-abcd-ef1234567890"
        valid, reason = server._validate_job_url(url)
        assert valid

    def test_ashby_direct_accepted(self):
        url = "https://jobs.ashbyhq.com/acmecorp/1a2b3c4d-5e6f-7890-abcd-ef1234567890"
        valid, reason = server._validate_job_url(url)
        assert valid

    def test_linkedin_job_view_accepted(self):
        url = "https://www.linkedin.com/jobs/view/senior-engineer-at-acme-1234567890"
        valid, reason = server._validate_job_url(url)
        assert valid

    def test_remoterocketship_company_job_accepted(self):
        url = "https://remoterocketship.com/us/company/acme-corp/jobs/senior-engineer"
        valid, reason = server._validate_job_url(url)
        assert valid

    def test_unknown_direct_link_accepted_with_warning(self):
        """A URL not matching any known pattern should be accepted with a warning."""
        url = "https://careers.someunknowncompany.com/jobs/12345"
        valid, reason = server._validate_job_url(url)
        assert valid
        assert "not a known" in reason.lower()

    def test_google_search_not_explicitly_rejected_but_still_accepted(self):
        """Google isn't in BAD_URL_PATTERNS so it passes through with a warning."""
        url = "https://www.google.com/search?q=software+engineer+jobs"
        valid, reason = server._validate_job_url(url)
        # Google isn't a blocked pattern — accepted with 'not a known' warning
        assert valid


# ===========================================================================
# 2. Dead listing detection (_check_url_alive)
# ===========================================================================

class TestCheckUrlAlive:
    """Tests for _check_url_alive — mocks urllib, no real network calls."""

    # --- Greenhouse API responses ---

    def test_greenhouse_live_true(self):
        body = json.dumps({"live": True, "status": "live"}).encode()
        mock_resp = _make_mock_response(body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            alive, reason = server._check_url_alive(
                "https://job-boards.greenhouse.io/acmecorp/jobs/123456"
            )
        assert alive is True
        assert "live" in reason.lower()

    def test_greenhouse_live_false(self):
        body = json.dumps({"live": False, "status": "closed"}).encode()
        mock_resp = _make_mock_response(body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            alive, reason = server._check_url_alive(
                "https://job-boards.greenhouse.io/acmecorp/jobs/123456"
            )
        assert alive is False
        assert "closed" in reason.lower() or "live=False" in reason

    def test_greenhouse_status_not_live(self):
        body = json.dumps({"live": True, "status": "closed"}).encode()
        mock_resp = _make_mock_response(body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            alive, reason = server._check_url_alive(
                "https://job-boards.greenhouse.io/acmecorp/jobs/789"
            )
        assert alive is False

    def test_greenhouse_404_returns_dead(self):
        with patch("urllib.request.urlopen", side_effect=_make_http_error(404)):
            alive, reason = server._check_url_alive(
                "https://job-boards.greenhouse.io/acmecorp/jobs/999"
            )
        assert alive is False
        assert "404" in reason

    def test_greenhouse_500_returns_alive_keep(self):
        """Server error — err on the side of keeping."""
        with patch("urllib.request.urlopen", side_effect=_make_http_error(500)):
            alive, reason = server._check_url_alive(
                "https://job-boards.greenhouse.io/acmecorp/jobs/999"
            )
        assert alive is True
        assert "keeping" in reason.lower()

    # --- Non-Greenhouse: direct GET logic ---

    def test_lever_404_returns_dead(self):
        with patch("urllib.request.urlopen", side_effect=_make_http_error(404)):
            alive, reason = server._check_url_alive(
                "https://jobs.lever.co/acmecorp/some-uuid"
            )
        assert alive is False
        assert "404" in reason

    def test_lever_410_returns_dead(self):
        with patch("urllib.request.urlopen", side_effect=_make_http_error(410)):
            alive, reason = server._check_url_alive(
                "https://jobs.lever.co/acmecorp/some-uuid"
            )
        assert alive is False
        assert "410" in reason

    def test_lever_200_returns_alive(self):
        mock_resp = _make_mock_response(b"<html>job posting</html>")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            alive, reason = server._check_url_alive(
                "https://jobs.lever.co/acmecorp/some-uuid"
            )
        assert alive is True

    def test_lever_403_kept_not_dead(self):
        """403 is ambiguous — treat as keep."""
        with patch("urllib.request.urlopen", side_effect=_make_http_error(403)):
            alive, reason = server._check_url_alive(
                "https://jobs.lever.co/acmecorp/some-uuid"
            )
        assert alive is True
        assert "keeping" in reason.lower()

    def test_connection_error_kept(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            alive, reason = server._check_url_alive("https://example.com/job/123")
        assert alive is True
        assert "keeping" in reason.lower()

    # --- Ashby ---

    def test_ashby_404_returns_dead(self):
        """First call (Ashby API) raises 404."""
        uuid = "1a2b3c4d-5e6f-7890-abcd-ef1234567890"
        with patch("urllib.request.urlopen", side_effect=_make_http_error(404)):
            alive, reason = server._check_url_alive(
                f"https://jobs.ashbyhq.com/acmecorp/{uuid}"
            )
        assert alive is False
        assert "404" in reason

    def test_ashby_200_returns_alive(self):
        uuid = "1a2b3c4d-5e6f-7890-abcd-ef1234567890"
        mock_resp = _make_mock_response(b"{}")
        mock_resp.status = 200
        with patch("urllib.request.urlopen", return_value=mock_resp):
            alive, reason = server._check_url_alive(
                f"https://jobs.ashbyhq.com/acmecorp/{uuid}"
            )
        assert alive is True


# ===========================================================================
# 3. Application CRUD (handle_log_application, handle_get_applications,
#    handle_update_application)
# ===========================================================================

class TestApplicationCRUD:

    def test_log_application_creates_db(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_log_application({"company": "Acme", "role": "Engineer"})
        assert result["success"] is True
        db_path = tmp_path / "data" / "applications.db"
        assert db_path.exists()

    def test_log_application_returns_applied_at(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_log_application({"company": "Acme", "role": "Engineer"})
        assert "applied_at" in result

    def test_log_application_optional_fields(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_log_application({
            "company": "Acme",
            "role": "Engineer",
            "salary": "$150k",
            "url": "https://example.com/job/1",
            "status": "screening",
            "notes": "Referral from Alice",
        })
        assert result["success"] is True

    def test_get_applications_empty_returns_empty_list(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_get_applications({})
        assert result["applications"] == []

    def test_get_applications_returns_logged_entry(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        result = server.handle_get_applications({})
        assert result["count"] == 1
        assert result["applications"][0]["company"] == "Acme"

    def test_get_applications_multiple_ordered_newest_first(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Alpha", "role": "SWE"})
        server.handle_log_application({"company": "Beta", "role": "SWE"})
        result = server.handle_get_applications({})
        companies = [a["company"] for a in result["applications"]]
        assert companies[0] == "Beta"  # most recent first

    def test_get_applications_status_filter(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE", "status": "applied"})
        server.handle_log_application({"company": "Beta", "role": "SWE", "status": "interview"})
        result = server.handle_get_applications({"status": "interview"})
        assert result["count"] == 1
        assert result["applications"][0]["company"] == "Beta"

    def test_update_application_status(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]

        result = server.handle_update_application({"id": app_id, "status": "interview"})
        assert result["success"] is True
        assert result["application"]["status"] == "interview"

    def test_update_application_notes(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]

        result = server.handle_update_application({"id": app_id, "notes": "Great phone screen"})
        assert result["application"]["notes"] == "Great phone screen"

    def test_update_application_updates_last_activity_date(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]
        result = server.handle_update_application({"id": app_id, "status": "rejected"})
        assert result["application"]["last_activity_date"] is not None

    def test_update_application_missing_id_returns_error(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_update_application({})
        assert "error" in result

    def test_update_application_invalid_id_returns_error(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        result = server.handle_update_application({"id": 9999, "status": "rejected"})
        assert "error" in result

    def test_update_application_no_fields_returns_error(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]
        result = server.handle_update_application({"id": app_id})
        assert "error" in result

    def test_update_application_response_received_bool(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]
        result = server.handle_update_application({"id": app_id, "response_received": True})
        assert result["application"]["response_received"] == 1

    # --- Deduplication note ---
    # handle_log_application does NOT enforce deduplication itself — that's in
    # handle_save_scouted_job and _is_duplicate_application. We test those in
    # the scouted jobs section below.


# ===========================================================================
# 4. Scouted Jobs (handle_save_scouted_job, handle_get_scouted_jobs,
#    handle_mark_jobs_ranked, handle_purge_dead_listings)
# ===========================================================================

VALID_GH_URL = "https://job-boards.greenhouse.io/acmecorp/jobs/4567890"
VALID_LEVER_URL = "https://jobs.lever.co/acmecorp/1a2b3c4d-5e6f-7890-abcd-ef1234567890"


class TestScoutedJobs:

    def test_save_scouted_job_success(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_save_scouted_job({
            "company": "Acme",
            "role": "SWE",
            "url": VALID_GH_URL,
        })
        assert result["saved"] is True

    def test_save_scouted_job_creates_file(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Acme", "role": "SWE", "url": VALID_GH_URL})
        scouted_path = tmp_path / "data" / "scouted_jobs.json"
        assert scouted_path.exists()
        data = json.loads(scouted_path.read_text())
        assert len(data) == 1
        assert data[0]["company"] == "Acme"

    def test_save_scouted_job_stores_date_found(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Acme", "role": "SWE", "url": VALID_GH_URL})
        scouted_path = tmp_path / "data" / "scouted_jobs.json"
        data = json.loads(scouted_path.read_text())
        assert "date_found" in data[0]
        assert data[0]["date_found"]  # non-empty

    def test_save_scouted_job_starts_unranked(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Acme", "role": "SWE", "url": VALID_GH_URL})
        scouted_path = tmp_path / "data" / "scouted_jobs.json"
        data = json.loads(scouted_path.read_text())
        assert data[0]["ranked"] is False

    def test_save_scouted_job_rejects_bad_url(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_save_scouted_job({
            "company": "Acme",
            "role": "SWE",
            "url": "https://builtin.com/jobs/software-engineer",
        })
        assert result["saved"] is False
        assert "Rejected" in result["reason"]

    def test_save_scouted_job_dedup_same_company_role(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Acme", "role": "SWE", "url": VALID_GH_URL})
        result = server.handle_save_scouted_job({
            "company": "Acme",
            "role": "SWE",
            "url": VALID_LEVER_URL,
        })
        assert result["saved"] is False
        assert "Duplicate" in result["reason"]

    def test_save_scouted_job_dedup_case_insensitive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Acme", "role": "SWE", "url": VALID_GH_URL})
        result = server.handle_save_scouted_job({
            "company": "ACME",
            "role": "swe",
            "url": VALID_LEVER_URL,
        })
        assert result["saved"] is False

    def test_save_scouted_job_dedup_against_applications(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        # Log to applications DB first
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        # Now try to scout the same job
        result = server.handle_save_scouted_job({
            "company": "Acme",
            "role": "SWE",
            "url": VALID_GH_URL,
        })
        assert result["saved"] is False
        assert "already applied" in result["reason"].lower() or "applications" in result["reason"].lower()

    def test_save_scouted_job_missing_required_fields(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_save_scouted_job({"company": "Acme"})
        assert result["saved"] is False

    def test_get_scouted_jobs_returns_all(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Alpha", "role": "SWE", "url": VALID_GH_URL})
        server.handle_save_scouted_job({"company": "Beta", "role": "PM", "url": VALID_LEVER_URL})
        result = server.handle_get_scouted_jobs({})
        assert result["count"] == 2

    def test_get_scouted_jobs_empty(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_get_scouted_jobs({})
        assert result["count"] == 0
        assert result["jobs"] == []

    def test_get_scouted_jobs_unranked_only_filter(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Alpha", "role": "SWE", "url": VALID_GH_URL})
        server.handle_save_scouted_job({"company": "Beta", "role": "PM", "url": VALID_LEVER_URL})
        # Mark all ranked
        server.handle_mark_jobs_ranked({})
        # Add one more unranked
        server.handle_save_scouted_job({
            "company": "Gamma",
            "role": "Staff SWE",
            "url": "https://careers.someunknowncompany.com/jobs/789",
        })
        result = server.handle_get_scouted_jobs({"unranked_only": True})
        assert result["count"] == 1
        assert result["jobs"][0]["company"] == "Gamma"

    def test_mark_jobs_ranked_marks_all(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Alpha", "role": "SWE", "url": VALID_GH_URL})
        server.handle_save_scouted_job({"company": "Beta", "role": "PM", "url": VALID_LEVER_URL})
        result = server.handle_mark_jobs_ranked({})
        assert result["marked"] == 2

    def test_mark_jobs_ranked_idempotent(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Alpha", "role": "SWE", "url": VALID_GH_URL})
        server.handle_mark_jobs_ranked({})
        result = server.handle_mark_jobs_ranked({})
        assert result["marked"] == 0  # nothing new to mark

    def test_mark_jobs_ranked_persisted_to_file(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "Alpha", "role": "SWE", "url": VALID_GH_URL})
        server.handle_mark_jobs_ranked({})
        scouted_path = tmp_path / "data" / "scouted_jobs.json"
        data = json.loads(scouted_path.read_text())
        assert all(j["ranked"] for j in data)

    # --- purge_dead_listings ---

    def test_purge_dead_listings_removes_dead(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "DeadCo", "role": "SWE", "url": VALID_GH_URL})
        server.handle_save_scouted_job({"company": "AliveCo", "role": "SWE", "url": VALID_LEVER_URL})

        def mock_check(url, **kwargs):
            if "acmecorp/jobs" in url:   # greenhouse URL = dead
                return False, "Greenhouse closed"
            return True, "HTTP 200"       # lever URL = alive

        monkeypatch.setattr(server, "_check_url_alive", mock_check)
        result = server.handle_purge_dead_listings({})
        assert result["removed_count"] == 1
        assert result["kept_count"] == 1
        assert result["removed"][0]["company"] == "DeadCo"

        # Verify file was updated
        scouted_path = tmp_path / "data" / "scouted_jobs.json"
        data = json.loads(scouted_path.read_text())
        assert len(data) == 1
        assert data[0]["company"] == "AliveCo"

    def test_purge_dead_listings_dry_run_does_not_delete(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "DeadCo", "role": "SWE", "url": VALID_GH_URL})

        monkeypatch.setattr(server, "_check_url_alive", lambda url, **kw: (False, "HTTP 404"))
        result = server.handle_purge_dead_listings({"dry_run": True})
        assert result["dry_run"] is True
        assert result["removed_count"] == 1

        # File should still have the entry (dry run)
        scouted_path = tmp_path / "data" / "scouted_jobs.json"
        data = json.loads(scouted_path.read_text())
        assert len(data) == 1

    def test_purge_dead_listings_empty_scouted(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_purge_dead_listings({})
        assert result["removed_count"] == 0
        assert "No scouted jobs found" in result["message"]

    def test_purge_keeps_connection_error_jobs(self, tmp_path, monkeypatch):
        """Connection errors should keep the listing (not false-positive delete)."""
        _patch_paths(tmp_path, monkeypatch)
        server.handle_save_scouted_job({"company": "MaybeCo", "role": "SWE", "url": VALID_GH_URL})
        monkeypatch.setattr(server, "_check_url_alive", lambda url, **kw: (True, "connection error (keeping)"))
        result = server.handle_purge_dead_listings({})
        assert result["kept_count"] == 1
        assert result["removed_count"] == 0


# ===========================================================================
# 5. Profile (handle_get_profile, handle_update_profile, handle_setup)
# ===========================================================================

MINIMAL_SETUP_PARAMS = {
    "name": "Jane Doe",
    "current_role": "SWE",
    "target_roles": ["Senior SWE"],
    "salary_floor": 120000,
    "remote_only": False,
    "location": "Seattle, WA",
    "dealbreakers": ["travel"],
    "github_url": "https://github.com/janedoe",
}


class TestProfile:

    def test_get_profile_returns_error_when_no_profile(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_get_profile({})
        assert "error" in result
        assert "No profile" in result["error"]

    def test_get_profile_returns_profile_when_exists(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        _make_profile(tmp_path)
        result = server.handle_get_profile({})
        assert result["name"] == "Test User"
        assert "error" not in result

    def test_setup_creates_profile(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_setup(MINIMAL_SETUP_PARAMS)
        assert result["success"] is True
        profile_path = tmp_path / "data" / "profile.json"
        assert profile_path.exists()

    def test_setup_persists_required_fields(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_setup(MINIMAL_SETUP_PARAMS)
        profile_path = tmp_path / "data" / "profile.json"
        data = json.loads(profile_path.read_text())
        assert data["name"] == "Jane Doe"
        assert data["salary_floor"] == 120000
        assert data["target_roles"] == ["Senior SWE"]

    def test_setup_existing_profile_requires_confirm(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_setup(MINIMAL_SETUP_PARAMS)
        # Second call without confirm_overwrite
        result = server.handle_setup({**MINIMAL_SETUP_PARAMS, "name": "John Doe"})
        assert result.get("exists") is True
        # Profile should not have changed
        profile_path = tmp_path / "data" / "profile.json"
        data = json.loads(profile_path.read_text())
        assert data["name"] == "Jane Doe"

    def test_setup_overwrite_with_confirm(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_setup(MINIMAL_SETUP_PARAMS)
        result = server.handle_setup({
            **MINIMAL_SETUP_PARAMS,
            "name": "John Doe",
            "confirm_overwrite": True,
        })
        assert result["success"] is True
        profile_path = tmp_path / "data" / "profile.json"
        data = json.loads(profile_path.read_text())
        assert data["name"] == "John Doe"

    def test_setup_optional_fields_saved(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        params = {**MINIMAL_SETUP_PARAMS, "resume_text": "My resume content"}
        server.handle_setup(params)
        profile_path = tmp_path / "data" / "profile.json"
        data = json.loads(profile_path.read_text())
        assert data["resume_text"] == "My resume content"

    def test_update_profile_merges_fields(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        _make_profile(tmp_path)
        result = server.handle_update_profile({"salary_floor": 200000})
        assert result["success"] is True
        assert result["profile"]["salary_floor"] == 200000
        # Other fields preserved
        assert result["profile"]["name"] == "Test User"

    def test_update_profile_no_profile_returns_error(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        result = server.handle_update_profile({"salary_floor": 200000})
        assert "error" in result

    def test_update_profile_empty_params_returns_error(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        _make_profile(tmp_path)
        result = server.handle_update_profile({})
        assert "error" in result

    def test_update_profile_multiple_fields(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        _make_profile(tmp_path)
        result = server.handle_update_profile({
            "unlisted_skills": ["Docker", "Kubernetes"],
            "developing_skills": ["Rust"],
        })
        assert "unlisted_skills" in result["updated_fields"]
        assert "developing_skills" in result["updated_fields"]
        assert result["profile"]["unlisted_skills"] == ["Docker", "Kubernetes"]

    def test_update_profile_persists_to_disk(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        _make_profile(tmp_path)
        server.handle_update_profile({"location": "New York, NY"})
        profile_path = tmp_path / "data" / "profile.json"
        data = json.loads(profile_path.read_text())
        assert data["location"] == "New York, NY"

    def test_update_profile_sets_updated_at(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        _make_profile(tmp_path)
        result = server.handle_update_profile({"location": "Boston"})
        assert "updated_at" in result["profile"]


# ===========================================================================
# 6. Additional edge cases and helpers
# ===========================================================================

class TestDuplicateDetection:
    """Direct tests of _is_duplicate_scouted and _is_duplicate_application."""

    def test_is_duplicate_scouted_match(self):
        jobs = [{"company": "Acme", "role": "Engineer"}]
        assert server._is_duplicate_scouted("Acme", "Engineer", jobs) is True

    def test_is_duplicate_scouted_case_insensitive(self):
        jobs = [{"company": "ACME", "role": "ENGINEER"}]
        assert server._is_duplicate_scouted("acme", "engineer", jobs) is True

    def test_is_duplicate_scouted_no_match(self):
        jobs = [{"company": "Acme", "role": "Engineer"}]
        assert server._is_duplicate_scouted("Beta", "PM", jobs) is False

    def test_is_duplicate_scouted_empty_list(self):
        assert server._is_duplicate_scouted("Acme", "SWE", []) is False

    def test_is_duplicate_application_no_db(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        # DB doesn't exist yet
        result = server._is_duplicate_application("Acme", "SWE")
        assert result is False

    def test_is_duplicate_application_match(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        assert server._is_duplicate_application("Acme", "SWE") is True

    def test_is_duplicate_application_case_insensitive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "ACME", "role": "SWE"})
        assert server._is_duplicate_application("acme", "swe") is True


class TestResolveResume:
    """Tests for _resolve_resume priority logic."""

    def test_resume_text_priority_over_path(self, tmp_path):
        profile = {
            "resume_text": "My inline resume",
            "resume_path": "/nonexistent/path/resume.txt",
        }
        content, source, err = server._resolve_resume(profile)
        assert content == "My inline resume"
        assert source == "resume_text"
        assert err is None

    def test_falls_back_to_path(self, tmp_path):
        resume_file = tmp_path / "resume.txt"
        resume_file.write_text("File-based resume", encoding="utf-8")
        profile = {"resume_path": str(resume_file)}
        content, source, err = server._resolve_resume(profile)
        assert content == "File-based resume"
        assert source == "resume_path"
        assert err is None

    def test_no_resume_returns_error(self):
        content, source, err = server._resolve_resume({})
        assert content is None
        assert err is not None

    def test_missing_file_returns_error(self, tmp_path):
        profile = {"resume_path": str(tmp_path / "nonexistent.txt")}
        content, source, err = server._resolve_resume(profile)
        assert content is None
        assert err is not None


class TestFollowUps:
    """Tests for handle_get_follow_ups."""

    def test_get_follow_ups_returns_overdue(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]
        # Set a past follow_up_due_date and status applied
        server.handle_update_application({
            "id": app_id,
            "follow_up_due_date": "2020-01-01",
            "status": "applied",
        })
        result = server.handle_get_follow_ups({})
        assert result["count"] == 1
        assert result["follow_ups"][0]["company"] == "Acme"

    def test_get_follow_ups_excludes_future_dates(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]
        server.handle_update_application({
            "id": app_id,
            "follow_up_due_date": "2099-12-31",
        })
        result = server.handle_get_follow_ups({})
        assert result["count"] == 0

    def test_get_follow_ups_excludes_non_applied_status(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]
        server.handle_update_application({
            "id": app_id,
            "follow_up_due_date": "2020-01-01",
            "status": "rejected",
        })
        result = server.handle_get_follow_ups({})
        assert result["count"] == 0

    def test_get_follow_ups_includes_days_since_applied(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        server.handle_log_application({"company": "Acme", "role": "SWE"})
        apps = server.handle_get_applications({})
        app_id = apps["applications"][0]["id"]
        server.handle_update_application({
            "id": app_id,
            "follow_up_due_date": "2020-01-01",
            "status": "applied",
        })
        result = server.handle_get_follow_ups({})
        assert "days_since_applied" in result["follow_ups"][0]


class TestScoutedJobsCap:
    """Test the SCOUTED_CAP trim logic in _write_scouted_jobs."""

    def test_write_scouted_jobs_trims_ranked_beyond_cap(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        # Build 55 ranked + 1 unranked = 56 total, cap is 50
        jobs = []
        for i in range(55):
            jobs.append({
                "company": f"Co{i}",
                "role": "SWE",
                "url": f"https://example.com/job/{i}",
                "ranked": True,
                "date_found": f"2026-01-{i+1:02d}T00:00:00+00:00" if i < 30 else f"2026-02-{i-29:02d}T00:00:00+00:00",
            })
        jobs.append({
            "company": "UnrankedCo",
            "role": "PM",
            "url": "https://example.com/job/unranked",
            "ranked": False,
            "date_found": "2026-03-01T00:00:00+00:00",
        })

        server._write_scouted_jobs(jobs)
        scouted_path = tmp_path / "data" / "scouted_jobs.json"
        data = json.loads(scouted_path.read_text())
        assert len(data) <= server.SCOUTED_CAP
        # Unranked job must be preserved
        companies = [j["company"] for j in data]
        assert "UnrankedCo" in companies


class TestInitDb:
    """Test database initialization and migration."""

    def test_init_db_creates_table(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        conn = server.init_db()
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='applications'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_init_db_migration_adds_missing_columns(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        # Create a "legacy" DB without the newer columns
        db_path = tmp_path / "data" / "applications.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "company TEXT, role TEXT, status TEXT, applied_at TEXT)"
        )
        conn.commit()
        conn.close()

        # init_db should add missing columns without error
        conn = server.init_db()
        cursor = conn.execute("PRAGMA table_info(applications)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "last_activity_date" in cols
        assert "follow_up_due_date" in cols
        assert "response_received" in cols
        assert "outcome" in cols
