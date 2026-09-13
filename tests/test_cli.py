"""CLI smoke tests."""

from click.testing import CliRunner

from crypto_lab.cli import main


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0
    assert "0.4.0" in result.output
    assert "PAPER" in result.output
    assert "4A" in result.output


def test_cli_init_db_and_health(tmp_path, monkeypatch):
    db = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    from crypto_lab.config.settings import get_settings
    from crypto_lab.data.database import reset_engine

    get_settings.cache_clear()
    reset_engine()

    runner = CliRunner()
    r1 = runner.invoke(main, ["init-db", "--database-url", f"sqlite:///{db}"])
    assert r1.exit_code == 0, r1.output
    assert "Schema OK" in r1.output

    get_settings.cache_clear()
    reset_engine()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    r2 = runner.invoke(main, ["health", "--json"])
    assert r2.exit_code == 0, r2.output
    assert "true" in r2.output.lower() or '"healthy": true' in r2.output


def test_cli_data_validate_sample(tmp_path, monkeypatch):
    db = tmp_path / "val.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    from crypto_lab.config.settings import get_settings
    from crypto_lab.data.database import reset_engine

    get_settings.cache_clear()
    reset_engine()
    runner = CliRunner()
    r = runner.invoke(main, ["data", "validate-sample", "--database-url", f"sqlite:///{db}"])
    assert r.exit_code == 0, r.output
    assert "stored" in r.output
