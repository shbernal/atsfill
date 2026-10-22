"""Survey rows end to end, each on a browser of its own: the probe tests share one per module."""

from atsfill.core import commands, paths
from scripts import survey
from tests import persona
from tests.fake import Fake


def test_a_refused_row_keeps_each_failing_checks_answer_and_read_back(
    server, tmp_path, monkeypatch
):
    fake = Fake()
    fake.typo = True
    monkeypatch.setattr(commands, "adapters", lambda: [fake])
    monkeypatch.setattr(survey, "adapters", lambda: [fake])
    monkeypatch.setattr(survey, "CORPUS", tmp_path / "corpus")
    monkeypatch.setattr(survey, "MAIN", tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    monkeypatch.setattr(persona, "answers", lambda fields, cv, photo: {"name": "Ada"})
    cv = persona.resume(tmp_path / "resume.pdf")
    data = paths.data_dir(tmp_path / "data")
    row = survey.survey(f"{server}/form.html", "Acme", None, data, cv, {})
    assert (row["status"], row["reason"]) == ("refused", "mismatch: name")
    assert row["mismatches"] == [{"field_id": "name", "answer": "Ada", "read_back": "Adax"}]
