"""CLI experiment demo + schema experiments table."""

from __future__ import annotations

from click.testing import CliRunner

from crypto_lab.cli import main
from crypto_lab.data.database import EXPECTED_TABLES, init_db, list_tables, reset_engine


def test_experiments_table_created(tmp_path):
    url = f"sqlite:///{tmp_path / 'e.db'}"
    reset_engine()
    engine = init_db(url)
    tables = list_tables(engine)
    assert "experiments" in EXPECTED_TABLES
    assert "experiments" in tables


def test_cli_experiment_demo(tmp_path, monkeypatch):
    db = tmp_path / "demo.db"
    out = tmp_path / "exp"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    from crypto_lab.config.settings import get_settings

    get_settings.cache_clear()
    reset_engine()
    runner = CliRunner()
    r = runner.invoke(
        main,
        [
            "experiment",
            "demo",
            "--out",
            str(out),
            "--database-url",
            f"sqlite:///{db}",
            "--bars",
            "80",
            "--seed",
            "3",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "scientific_conclusion" in r.output
    assert (out / "demo_summary.json").exists()
