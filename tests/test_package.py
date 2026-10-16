import pytest

from atsfill.cli import parser


def test_send_requires_answers():
    with pytest.raises(SystemExit) as exit:
        parser().parse_args(["send", "https://example.com"])
    assert exit.value.code == 2
