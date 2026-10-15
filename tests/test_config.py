"""Config loading, and the untracked overlay that keeps private figures out.

`config/criteria.yml` is committed, so it holds worked examples rather than a
real budget or a real office postal code. The overlay is what lets somebody
run this against their own figures without those figures ever reaching a
commit, which makes it a privacy mechanism rather than a convenience — a
merge that quietly dropped an override would send the example budget into a
report, and one that leaked the override's path into a tracked file would
defeat the point.
"""

from __future__ import annotations

import pytest
import yaml

from nhf import config as config_module


@pytest.fixture(autouse=True)
def _clean_config_cache():
    """Leave the real config loaded for every other test in the session.

    `load_config` is cached for the process, so a test that points it at a
    temporary directory poisons the cache for everything after it. Reloading
    the module was worse: other modules hold a reference to the function they
    imported, and rebinding the global left half the suite reading a config
    directory that had been deleted.
    """
    config_module.load_config.cache_clear()
    yield
    config_module.load_config.cache_clear()


def _config_dir(tmp_path, base: dict, local: dict | None = None):
    """A config directory with the four files the loader expects."""
    (tmp_path / "criteria.yml").write_text(yaml.safe_dump(base))
    for name in ("datasets.yml", "policy.yml", "future_mrt.yml"):
        (tmp_path / name).write_text("{}\n")
    if local is not None:
        (tmp_path / "criteria.local.yml").write_text(yaml.safe_dump(local))
    return tmp_path


def _load(monkeypatch, tmp_path):
    # Patched in place rather than through the environment variable, which is
    # only read when the module is first imported.
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    config_module.load_config.cache_clear()
    return config_module.load_config()


class TestTheOverlay:
    def test_the_committed_file_is_used_when_there_is_no_overlay(
        self, monkeypatch, tmp_path
    ):
        _config_dir(tmp_path, {"household": {"budget": 500000}})
        assert _load(monkeypatch, tmp_path).criteria["household"]["budget"] == 500000

    def test_an_overlay_replaces_a_value(self, monkeypatch, tmp_path):
        _config_dir(
            tmp_path,
            {"household": {"budget": 500000}},
            {"household": {"budget": 650000}},
        )
        assert _load(monkeypatch, tmp_path).criteria["household"]["budget"] == 650000

    def test_it_merges_rather_than_replaces_the_whole_block(
        self, monkeypatch, tmp_path
    ):
        """An overlay that names only the budget must not silently drop the
        rest of the household block with it."""
        _config_dir(
            tmp_path,
            {"household": {"budget": 500000, "holding_period_years": 20}},
            {"household": {"budget": 650000}},
        )
        household = _load(monkeypatch, tmp_path).criteria["household"]
        assert household == {"budget": 650000, "holding_period_years": 20}

    def test_a_list_is_replaced_whole(self, monkeypatch, tmp_path):
        """Naming `offices` means naming all of them. Appending instead would
        leave the committed example offices in a run that overrode them, and a
        commute would be computed to a building nobody goes to."""
        _config_dir(
            tmp_path,
            {"household": {"offices": [{"code": "office_a", "postal": "179103"}]}},
            {"household": {"offices": [{"code": "mine", "postal": "123456"}]}},
        )
        offices = _load(monkeypatch, tmp_path).criteria["household"]["offices"]
        assert offices == [{"code": "mine", "postal": "123456"}]

    def test_it_recurses_into_nested_mappings(self, monkeypatch, tmp_path):
        _config_dir(
            tmp_path,
            {"fair_value": {"comp_months": 24, "nearby_radius_m": 800}},
            {"fair_value": {"nearby_radius_m": 500}},
        )
        assert _load(monkeypatch, tmp_path).criteria["fair_value"] == {
            "comp_months": 24,
            "nearby_radius_m": 500,
        }

    def test_an_empty_overlay_changes_nothing(self, monkeypatch, tmp_path):
        (tmp_path / "criteria.local.yml").write_text("")
        _config_dir(tmp_path, {"household": {"budget": 500000}}, None)
        (tmp_path / "criteria.local.yml").write_text("")
        assert _load(monkeypatch, tmp_path).criteria["household"]["budget"] == 500000


class TestWhatIsCommitted:
    def test_the_committed_criteria_name_no_real_office(self):
        """The repository may be public. A postal code says which building
        somebody is in at nine in the morning, and a label can carry a name."""
        text = (config_module.REPO_ROOT / "config" / "criteria.yml").read_text()
        for private in ("109679", "098544", "Daniel", "Connie"):
            assert private not in text, f"{private} is still in criteria.yml"

    def test_the_overlay_is_ignored_by_git(self):
        ignore = (config_module.REPO_ROOT / ".gitignore").read_text()
        assert "config/*.local.yml" in ignore
