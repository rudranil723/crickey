"""
tests/test_output.py
--------------------
pytest-compatible tests that verify the quality of scraped JSON output files.

Run with:
    python -m pytest tests/ -v

These tests check both the schedule and any match-level output files present
in the output/ directory. They are designed to pass even when no matches have
been scraped yet (graceful skip on missing files).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

OUTPUT_DIR = Path("output")
SCHEDULE_FILE = OUTPUT_DIR / "schedule.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load(path: Path):
    if not path.exists():
        pytest.skip(f"File not found (run a scrape first): {path}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _scraped_match_dirs() -> list[Path]:
    """Return all match output directories that have a scorecard_latest.json."""
    if not OUTPUT_DIR.exists():
        return []
    return [
        d for d in OUTPUT_DIR.iterdir()
        if d.is_dir() and (d / "scorecard_latest.json").exists()
    ]


# ── Schedule tests ────────────────────────────────────────────────────────────

class TestSchedule:
    def test_schedule_is_list(self):
        data = _load(SCHEDULE_FILE)
        assert isinstance(data, list), "schedule.json should be a JSON array"

    def test_schedule_has_matches(self):
        data = _load(SCHEDULE_FILE)
        assert len(data) >= 1, f"Expected at least 1 match, got {len(data)}"

    def test_schedule_match_ids_non_empty(self):
        data = _load(SCHEDULE_FILE)
        for m in data:
            assert m.get("match_id"), f"match_id empty in: {m}"

    def test_schedule_team_names_non_empty(self):
        data = _load(SCHEDULE_FILE)
        for m in data:
            assert m.get("team_a"), f"team_a missing in: {m.get('match_id')}"
            assert m.get("team_b"), f"team_b missing in: {m.get('match_id')}"

    def test_schedule_status_valid(self):
        valid = {"upcoming", "live", "completed", "unknown"}
        data = _load(SCHEDULE_FILE)
        for m in data:
            assert m.get("status") in valid, (
                f"Invalid status '{m.get('status')}' in {m.get('match_id')}"
            )

    def test_schedule_slug_based_match_id(self):
        """match_id must be derived from the full slug (not just team names)
        so double-headers between same teams same day are distinct (Task 4c)."""
        data = _load(SCHEDULE_FILE)
        ids = [m["match_id"] for m in data if m.get("match_id")]
        assert len(ids) == len(set(ids)), "Duplicate match_ids detected in schedule"


# ── Per-match tests (parametrised over all scraped match dirs) ─────────────────

def _match_dir_ids():
    dirs = _scraped_match_dirs()
    return [d.name for d in dirs] if dirs else ["__no_matches__"]


@pytest.mark.parametrize("match_id", _match_dir_ids())
class TestMatchInfo:
    def test_match_info_exists(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        path = OUTPUT_DIR / match_id / "match_info.json"
        assert path.exists(), f"match_info.json missing for {match_id}"

    def test_match_info_has_series(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        data = _load(OUTPUT_DIR / match_id / "match_info.json")
        assert "series" in data, "match_info.json missing 'series' key"

    def test_match_info_has_match_id(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        data = _load(OUTPUT_DIR / match_id / "match_info.json")
        assert data.get("match_id"), "match_info.json missing 'match_id'"


@pytest.mark.parametrize("match_id", _match_dir_ids())
class TestSquads:
    def test_squads_exists(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        path = OUTPUT_DIR / match_id / "squads.json"
        assert path.exists(), f"squads.json missing for {match_id}"

    def test_squads_structure(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        data = _load(OUTPUT_DIR / match_id / "squads.json")
        teams = data.get("teams", [])
        assert isinstance(teams, list), "squads.teams must be a list"

    def test_squads_teams_have_names(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        data = _load(OUTPUT_DIR / match_id / "squads.json")
        for team in data.get("teams", []):
            assert team.get("team_name"), f"team_name empty in squads for {match_id}"


@pytest.mark.parametrize("match_id", _match_dir_ids())
class TestScorecard:
    def test_scorecard_exists(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        path = OUTPUT_DIR / match_id / "scorecard_latest.json"
        assert path.exists(), f"scorecard_latest.json missing for {match_id}"

    def test_scorecard_has_innings_key(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        data = _load(OUTPUT_DIR / match_id / "scorecard_latest.json")
        assert "innings" in data, "scorecard missing 'innings' key"
        assert isinstance(data["innings"], list), "'innings' must be a list"

    def test_scorecard_innings_structure(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        data = _load(OUTPUT_DIR / match_id / "scorecard_latest.json")
        innings = data.get("innings", [])
        if not innings:
            pytest.skip(f"No innings data yet for {match_id}")
        inn = innings[0]
        assert "batting" in inn, "innings[0] missing 'batting'"
        assert "bowling" in inn, "innings[0] missing 'bowling'"
        assert "extras" in inn, "innings[0] missing 'extras'"

    def test_scorecard_batting_entries_have_names(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        data = _load(OUTPUT_DIR / match_id / "scorecard_latest.json")
        innings = data.get("innings", [])
        if not innings:
            pytest.skip(f"No innings data yet for {match_id}")
        for batter in innings[0].get("batting", []):
            assert batter.get("player"), f"Batting entry missing 'player' in {match_id}"


@pytest.mark.parametrize("match_id", _match_dir_ids())
class TestLive:
    def test_live_exists_if_present(self, match_id):
        if match_id == "__no_matches__":
            pytest.skip("No scraped matches found in output/")
        path = OUTPUT_DIR / match_id / "live_latest.json"
        if not path.exists():
            pytest.skip(f"No live_latest.json for {match_id} (not a live match)")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "status_text" in data, "live_latest.json missing 'status_text'"
        assert "batters_on_crease" in data, "live_latest.json missing 'batters_on_crease'"
        assert "recent_balls" in data, "live_latest.json missing 'recent_balls'"


# ── LiveScore model field test (unit-like) ────────────────────────────────────

def test_livescore_model_has_interruption_reason():
    """Task 4a: Confirm the LiveScore model has the interruption_reason field."""
    from models.match import LiveScore
    import inspect
    fields = LiveScore.model_fields
    assert "interruption_reason" in fields, (
        "LiveScore model is missing 'interruption_reason' field (Task 4a)"
    )
    # Should be Optional (allow None)
    field = fields["interruption_reason"]
    assert field.default is None, "interruption_reason should default to None"


def test_scheduler_state_serialisation():
    """Task 5: Confirm MatchJobRegistry can round-trip its state."""
    # Import here to avoid side-effects at module level
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scheduler import MatchJobRegistry
    reg = MatchJobRegistry()
    reg.mark_known("match-a")
    reg.mark_known("match-b")
    reg.mark_static_done("match-a")
    reg.mark_completed("match-b")
    state = reg.to_dict()
    assert "known" in state and "static_done" in state and "completed" in state

    reg2 = MatchJobRegistry()
    reg2.restore_from_dict(state)
    assert reg2.is_known("match-a")
    assert reg2.is_known("match-b")
    assert reg2.is_static_done("match-a")
    assert reg2.is_completed("match-b")
    assert not reg2.is_static_done("match-b")
