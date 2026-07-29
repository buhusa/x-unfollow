from typer.testing import CliRunner

from x_unfollow import __version__
from x_unfollow.cli import app


def test_package_imports():
    assert __version__


def test_help_command_renders():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "x-unfollow" in result.output


def test_module_entrypoint_imports():
    import x_unfollow.__main__ as module_entrypoint

    assert module_entrypoint.main is not None
