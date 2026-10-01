import subprocess
import sys

import pytest

from assertwatch import __version__
from assertwatch.cli import EXIT_USAGE, main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"assertwatch {__version__}"


def test_version_is_installed_metadata():
    assert __version__ == "0.0.1"


def test_no_command_prints_help_and_exits_usage(capsys):
    assert main([]) == EXIT_USAGE
    assert "usage: assertwatch" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["init"])
def test_unimplemented_commands_exit_usage(command, capsys):
    assert main([command]) == EXIT_USAGE
    assert "not implemented yet" in capsys.readouterr().err


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "assertwatch", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"assertwatch {__version__}"
