"""The Teamtailor adapter on tests/pages/teamtailor, served as an employer's career site."""

import io
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from atsfill.adapters.teamtailor import Teamtailor, _stored_name
from atsfill.core import browser, matching
from atsfill.core.adapter import Held, Unsupported
from atsfill.core.results import Answer, FieldType, Is

PAGE = Path(__file__).parent / "pages/teamtailor/new.html"
JOB = "https://careers.example.com/fr/jobs/1234567-solution-engineer"
TAKEOVER = 7654321  # a job whose career site opens its cookie banner as a modal
EMAIL_LINK = "/applications/email_verification_needed"


VIDEO_ID, SGID = 4401, "c2dpZA--4401"
videos: dict[str, str] = {}  # sgid: the stored clip's file name


def serve(route) -> None:
    """The career site, and Teamtailor's videos: one created from the posted clip, read back
    by its sgid under the name its storage address ends in."""
    request, path = route.request, urlsplit(route.request.url).path
    if f"/jobs/{TAKEOVER}" in path and path.endswith("/applications/new"):
        banner = 'data-controller="common--cookies--alert"'
        page = PAGE.read_text().replace(
            banner, f'{banner} data-common--cookies--alert-takeover-modal-value="true"'
        )
        route.fulfill(content_type="text/html", body=page)
    elif path.endswith("/applications/new"):
        route.fulfill(path=PAGE)
    elif path.endswith(EMAIL_LINK):
        route.fulfill(path=PAGE.with_name("email_verification_needed.html"))
    elif path == "/videos" and request.method == "POST":
        videos[SGID] = request.post_data_json["video"]["file"].rsplit("/", 1)[-1]
        route.fulfill(json={"id": VIDEO_ID, "sgid": SGID})
    elif path.startswith("/videos/"):
        sgid = path.rsplit("/", 1)[-1]
        route.fulfill(json={"id": VIDEO_ID, "file_file_name": videos[sgid]})
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
    return adapter, adapter.browser_read(page)


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


def test_browser_read(opened):
    fields = {f.id: f for f in opened[1]}
    assert list(fields) == [
        "candidate[first_name]",
        "candidate[email]",
        "candidate[resume_remote_url]",
        "candidate[file_remote_url]",
        "candidate[location][query]",
        "candidate[location_ids][]",
        "candidate[consent_given]",
        "candidate[consent_given_future_jobs]",
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
    assert fields["question:108"].label == "À partir de quand ?"
    assert fields["question:108"].shown_when == Is("question:101", ["Oui"])
    assert fields["question:108"].required  # once shown
    assert fields["question:105"].options == ["Samedi", "Jour d'astreinte"]
    assert fields["question:106"].options == [str(n) for n in range(11)]


def test_request_read_lists_the_same_fields(opened, monkeypatch):
    _, fields = opened
    # As served: the drop zones' file inputs and the video's accepted types come from scripts.
    served = re.sub(r'<input type="file"[^>]*id="candidate_\w+"[^>]*>', "", PAGE.read_text())
    served = re.sub(r'(<div data-controller="forms--inputs--upload">)<input[^>]*>', r"\1", served)
    served = served.replace(' accept=".mp4,.mov,.webm"', "")
    fetched = []

    class Response(io.BytesIO):
        url = "https://acme.teamtailor.com/fr/jobs/1234567/applications/new"

        def __enter__(self):
            return self

    def urlopen(request, timeout):
        fetched.append(request.full_url)
        return Response(served.encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert Teamtailor().request_read(JOB) is None  # a custom domain is known by its page
    by_request = Teamtailor().request_read("https://acme.teamtailor.com/fr/jobs/1234567-x?a=b")
    assert fetched == ["https://acme.teamtailor.com/fr/jobs/1234567-x/applications/new"]
    assert by_request == [replace(f, accept=None) if f.id == "question:107" else f for f in fields]


def test_a_file_question_is_a_known_gap(opened):
    unfillable = {f.id: f.unfillable for f in opened[1] if f.unfillable}
    assert unfillable == {
        "question:109": "unsupported_rendering: file_question: question:109 is a question's "
        "file drop zone",
    }


def test_a_video_question_takes_a_clip_file(opened):
    video = next(f for f in opened[1] if f.id == "question:107")
    assert (video.type, video.accept) == (FieldType.FILE, [".mp4", ".mov", ".webm"])


def test_an_unknown_question_kind_is_unhandled():
    raw = {"id": "question:1", "kind": "signature", "question": True, "label": "", "required": 1}
    field = Teamtailor()._field(raw)
    assert field.unfillable == "unhandled_rendering: question:1 is a Teamtailor signature question"


def test_every_answer_reads_back_past_the_cookie_banner(opened, page, resume, tmp_path):
    clip = tmp_path / "Présentation Ada.mp4"
    clip.write_bytes(b"\x00\x00\x00\x1cftypisom")
    answers = {
        "candidate[first_name]": "Ada",
        "candidate[email]": "ada@example.com",
        "candidate[resume_remote_url]": resume,
        "candidate[file_remote_url]": resume,
        "candidate[location][query]": "London",
        "candidate[location_ids][]": ["Lyon"],
        "candidate[consent_given]": "Yes",
        "candidate[consent_given_future_jobs]": "Yes",
        "question:100": "Oui",
        "question:101": "Oui",
        "question:108": "Lundi",
        "question:102": "Site carrière",
        "question:105": ["Samedi", "Jour d'astreinte"],
        "question:106": "7",
        "question:103": ["Français", "Anglais"],
        "question:104": "2026-11-02",
        "question:107": str(clip),
    }
    adapter, fields = opened
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert set(adapter.shown_fields(page)) <= {f.id for f in fields}  # an upload's address too


def test_a_cookie_banner_open_as_a_modal_is_declined_before_the_address(page):
    adapter = Teamtailor()
    page.goto(f"https://careers.example.com/fr/jobs/{TAKEOVER}-role")
    fields = adapter.browser_read(page)
    answers: dict[str, Answer] = {"question:100": "Oui", "candidate[location][query]": "London"}
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert not page.locator("dialog[open]").count()


def test_a_disqualifying_answer_leaves_the_form_locked(opened, page):
    with pytest.raises(Unsupported, match="not_qualified"):
        opened[0].fill(page, {"question:100": "Non", "candidate[first_name]": "Ada"})


def test_the_page_shows_a_follow_up_once_its_earlier_answer_asks_it(opened, page):
    adapter, _ = opened
    assert "question:108" not in adapter.shown_fields(page)
    adapter.fill(page, {"question:100": "Oui", "question:101": "Oui"})
    assert "question:108" in adapter.shown_fields(page)


def test_submit_returns_the_thanks_page(opened, page):
    opened[0].fill(page, {"question:100": "Oui"})
    application = "1b8f2c3e-0000-4000-8000-00000000a456"
    assert opened[0].submit(page) == f"{JOB}/applications/{application}/thanks"


def test_submit_stops_on_the_emailed_verification_link(opened, page):
    def verify(route):  # the employer holds the application until the emailed link is clicked
        route.fulfill(status=302, headers={"location": JOB + EMAIL_LINK})

    opened[0].fill(page, {"question:100": "Oui"})
    page.route("**/thanks", verify)
    try:
        with pytest.raises(Held):
            opened[0].submit(page)
    finally:
        page.unroute("**/thanks", verify)


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("Jane DOE - Resume - Acme.pdf", "jane_doe_-_resume_-_acme.pdf"),
        ("CV Élodie (2026)  v2.pdf", "cv_elodie_2026_v2.pdf"),
        ("ABC--x__y  z.pdf", "abc--x_y_z.pdf"),
    ],
)
def test_a_file_reads_back_under_the_name_teamtailor_stores(name, stored):
    assert _stored_name(name) == stored
