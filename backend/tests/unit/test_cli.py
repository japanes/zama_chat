"""The CLI builds at all (typer rejects some option types only when the app starts)."""

import pytest
import typer
from typer.testing import CliRunner

from app.cli import _price, app


@pytest.mark.parametrize(
    "args",
    [["--help"], ["models", "--help"], ["models", "add", "--help"], ["create-user", "--help"]],
)
def test_help(args):
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output


def test_price():
    assert str(_price("0.10")) == "0.10"
    for bad in ("abc", "-1", "NaN"):
        with pytest.raises(typer.BadParameter):
            _price(bad)
