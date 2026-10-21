"""The Teamtailor adapter on tests/pages/teamtailor, served as an employer's career site."""

from pathlib import Path

import pytest

from atsfill.adapters.teamtailor import Teamtailor, _stored_name
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import FieldType

PAGE = Path(__file__).parent / "pages/teamtailor/new.html"
JOB = "https://careers.example.com/fr/jobs/1234567-solution-engineer"


def serve(route) -> None:
    if route.request.url.endswith("/applications/new"):
        route.fulfill(path=PAGE)
    else:
        route.fulfill(content_type="text/html", body="<a href='https://www.teamtailor.com/'></a>")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://careers.example.com/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[Teamtailor, list]:
    adapter = Teamtailor()
    page.goto(JOB + "?utm_source=LinkedIn")
    assert adapter.detect(page.url, page)
    return adapter, adapter.read_form(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_detection_and_posting():
    adapter = Teamtailor()
    assert adapter.detect("https://acme.teamtailor.com/jobs/1-role", None)
    assert not adapter.detect(JOB, None)  # a custom domain shows itself on the page
    assert not adapter.detect("https://acme.teamtailor.com/", None)
    assert (
        adapter.posting(JOB + "/applications/new?x=1") == "https://careers.example.com/jobs/1234567"
    )


def test_read_form(opened):
    fields = {f.id: f for f in opened[1]}
    assert list(fields) == [
        "candidate[first_name]",
        "candidate[email]",
        "candidate[resume_remote_url]",
        "candidate[file_remote_url]",
        "candidate[location][query]",
        "candidate[location_ids][]",
        "candidate[consent_given]",
        "question:100",
        "question:101",
        "question:108",
        "question:102",
        "question:105",
        "question:106",
        "question:107",
        "question:109",
        "question:103",
        "question:104",
    ]
    assert fields["candidate[first_name]"].label == "Prénom"
    assert fields["candidate[first_name]"].max_length == 50
    assert fields["candidate[consent_given]"].required
    assert fields["candidate[consent_given]"].label == "J'accepte la politique de confidentialité."
    assert fields["question:101"].options == ["Oui", "Non"]
    assert fields["question:102"].options == ["LinkedIn", "Site carrière"]
    assert not fields["question:102"].required
    assert fields["candidate[resume_remote_url]"].accept == [".pdf", ".docx"]
    assert fields["candidate[location][query]"].type is FieldType.TYPEAHEAD
    assert fields["candidate[location_ids][]"].options == ["Paris", "Lyon"]
    assert fields["candidate[location_ids][]"].required
    assert fields["question:100"].required  # qualifying questions are always answered
    assert (
        fields["question:108"].label
        == 'À partir de quand ? (asked when "Êtes-vous disponible ?" is "Oui")'
    )
    assert not fields["question:108"].required  # only once an earlier answer asks it
    assert fields["question:105"].options == ["Samedi", "Dimanche"]
    assert fields["question:106"].options == [str(n) for n in range(11)]


def test_a_video_or_file_question_is_a_known_gap(opened):
    unfillable = {f.id: f.unfillable for f in opened[1] if f.unfillable}
    assert unfillable == {
        "question:107": "unsupported_rendering: video_upload: question:107 is a video, "
        "recorded or uploaded",
        "question:109": "unsupported_rendering: file_question: question:109 is a question's "
        "file drop zone",
    }


def test_an_unknown_question_kind_is_unhandled():
    raw = {"id": "question:1", "kind": "signature", "question": True, "label": "", "required": 1}
    field = Teamtailor()._field(raw)
    assert field.unfillable == "unhandled_rendering: question:1 is a Teamtailor signature question"


def test_every_answer_reads_back_past_the_cookie_banner(opened, page, resume):
    answers = {
        "candidate[first_name]": "Ada",
        "candidate[email]": "ada@example.com",
        "candidate[resume_remote_url]": resume,
        "candidate[file_remote_url]": resume,
        "candidate[location][query]": "London",
        "candidate[location_ids][]": ["Lyon"],
        "candidate[consent_given]": "Yes",
        "question:100": "Oui",
        "question:101": "Oui",
        "question:108": "Lundi",
        "question:102": "Site carrière",
        "question:105": ["Samedi", "Dimanche"],
        "question:106": "7",
        "question:103": ["Français", "Anglais"],
        "question:104": "2026-11-02",
    }
    adapter, fields = opened
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_a_disqualifying_answer_leaves_the_form_locked(opened, page):
    with pytest.raises(Unsupported, match="not_qualified"):
        opened[0].fill(page, {"question:100": "Non", "candidate[first_name]": "Ada"})


def test_an_unanswered_follow_up_stops_the_fill(opened, page):
    with pytest.raises(Unsupported, match="missing_answer"):
        opened[0].fill(page, {"question:100": "Oui", "question:101": "Oui"})


def test_a_follow_up_not_asked_is_not_filled(opened, page):
    adapter, fields = opened
    answers = {"question:100": "Oui", "question:101": "Non", "question:108": "Lundi"}
    adapter.fill(page, answers)
    assert "question:108" not in adapter.read_back(page)


def test_submit_returns_the_thanks_page(opened, page):
    opened[0].fill(page, {"question:100": "Oui"})
    application = "1b8f2c3e-0000-4000-8000-00000000a456"
    assert opened[0].submit(page) == f"{JOB}/applications/{application}/thanks"


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("Jane DOE - Resume - Acme.pdf", "jane_doe_-_resume_-_acme.pdf"),
        ("CV Élodie (2026)  v2.pdf", "cv_elodie_2026_v2.pdf"),
        ("Jane_O'Doe, CV+final.pdf", "jane_o_doe_cv_final.pdf"),
        ("ñandú ça.Résumé.pdf", "nandu_ca.resume.pdf"),
        ("ABC--x__y  z.pdf", "abc--x_y_z.pdf"),
    ],
)
def test_a_file_reads_back_under_the_name_teamtailor_stores(name, stored):
    assert _stored_name(name) == stored
