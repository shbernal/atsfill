"""The Phenom adapter on tests/pages/phenom, served as careers.acme.example with its
configuration on Phenom's CDN."""

import io
import json
from pathlib import Path

import pytest

from atsfill.adapters import phenom
from atsfill.adapters.phenom import Phenom, _Config
from atsfill.core import browser, matching
from atsfill.core.adapter import NotTaken, Unsupported
from atsfill.core.results import AllOf, AnyOf, FieldType, Is, OptionSet

PAGES = Path(__file__).parent / "pages/phenom"
JOB = "https://careers.acme.example/us/en/job/ACMEUS1001EXTERNALENUS/Data-Analyst"
APPLY = "https://careers.acme.example/us/en/apply?jobSeqNo=ACMEUS1001EXTERNALENUS"
CONFIG = f"{phenom.CDN}/ACMEUS/workday/external/careersite/singlepage"
REFERENCES = json.loads((PAGES / "references.json").read_text())


def served(url: str, body: bytes | str | None) -> bytes | None:
    """What the fixture serves at `url`: a page, the configuration, a loader's answer."""
    if url.endswith("/applySubmit"):
        posted = json.loads(body or "{}")  # a step, else a loader
        key = posted["formData"].get("applyddokey") or posted["ddoKey"]
        answer = REFERENCES[key]
        if key == "getCountrySpecificFields":  # each country's own
            answer = answer[posted["formData"]["countryCode"]]
        return json.dumps(answer).encode()
    if url.startswith(CONFIG):
        name = url.rsplit("/", 1)[-1]
        return (PAGES / name).read_bytes() if (PAGES / name).exists() else None
    if url.startswith("https://careers.acme.example/"):
        return (PAGES / ("apply.html" if "/apply" in url else "job.html")).read_bytes()
    return None


def route(route):
    body = served(route.request.url, route.request.post_data)
    if body is None:
        route.fulfill(status=404)
    else:
        route.fulfill(body=body, headers={"Access-Control-Allow-Origin": "*"})


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://careers.acme.example/**", route)
        page.context.route(f"{phenom.CDN}/**", route)
        yield page


@pytest.fixture
def opened(page) -> tuple[Phenom, list]:
    adapter = Phenom()
    page.goto(JOB)
    assert adapter.detect(page.url, page)
    return adapter, adapter.browser_read(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_detection_and_posting():
    adapter = Phenom()
    assert adapter.detect(JOB, None)
    assert adapter.detect(APPLY + "&step=2", None)
    assert not adapter.detect("https://careers.acme.example/us/en/search-results", None)
    assert adapter.posting(JOB) == adapter.posting(APPLY + "&step=2&stepname=agreement")


def test_browser_read_lists_every_step_from_the_configuration(opened):
    adapter, read = opened
    fields = {f.id: f for f in read}
    assert list(fields) == [
        "resume",
        "firstName",
        "country",
        "cntryFields.city",
        "cntryFields.region",
        "state",
        "phoneWidget.countryPhoneCode",
        "phoneWidget.phoneNumber",
        "sourceType",
        "applicantSource",
        "nationality",
        "emailAgreement",
        "smsOptIn",
        "authorized",
        "contactBy",
        "startDate",
        "experienceData[0].title",
        "experienceData[0].fromTo.startDate",
        "experienceData[0].fromTo.endDate",
        "educationData[0].schoolName",
        "educationData[0].fromTo.startDate",
        "educationData[0].fromTo.endDate",
        "jsqData.Q1.a",
        "jsqData.Q1.a_dot_a",
        "yearsOfExperience",
        "comments",
        "custAgreement",
    ]  # no heading, hidden value, French-only box or entry shown only once Add is clicked
    resume = fields["resume"]
    assert resume.required  # a summary is asked without one, which is not read
    assert resume.accept == [".doc", ".docx", ".pdf", ".txt"]
    assert fields["firstName"].label == "First Name" and fields["firstName"].required
    assert fields["country"].options == ["Canada", "United States"]
    # each country's fields, a request a country
    assert fields["cntryFields.city"].shown_when is None and fields["cntryFields.city"].required
    region = fields["cntryFields.region"]
    assert region.shown_when == Is("country", ["Canada"]) and region.required
    assert region.options == ["Alberta", "Ontario"]
    # the list under the country before any; any other country's costs a request
    assert fields["state"].label == "State" and fields["state"].options is None
    assert fields["state"].option_sets == [
        OptionSet(Is("country", ["United States"]), ["Texas", "Utah"])
    ]
    assert fields["phoneWidget.phoneNumber"].type is FieldType.PHONE
    assert fields["phoneWidget.phoneNumber"].required
    assert fields["sourceType"].options == ["Job Board", "Referral"]
    assert fields["applicantSource"].options is None
    assert fields["applicantSource"].option_sets == [
        OptionSet(Is("sourceType", ["Job Board"]), ["LinkedIn", "Indeed"]),  # not a hidden one
        OptionSet(Is("sourceType", ["Referral"]), ["Employee"]),
    ]
    assert fields["nationality"].options == ["American", "Canadian"]
    assert fields["emailAgreement"].options == ["Yes", "No"]
    assert fields["smsOptIn"].options == ["No", "Yes"] and not fields["smsOptIn"].required
    assert fields["smsOptIn"].label == "Text me about my application"
    assert fields["authorized"].options == ["Yes", "No"] and fields["authorized"].required
    assert fields["contactBy"].type is FieldType.MULTI_CHOICE
    assert fields["startDate"].type is FieldType.DATE
    assert fields["experienceData[0].fromTo.startDate"].label == "From (MM/YYYY)"
    assert not fields["experienceData[0].fromTo.endDate"].required
    assert fields["educationData[0].fromTo.endDate"].label == "To (year)"
    assert fields["educationData[0].schoolName"].type is FieldType.TYPEAHEAD
    assert fields["jsqData.Q1.a"].options == ["Yes", "No"]
    follow_up = fields["jsqData.Q1.a_dot_a"]
    assert follow_up.label == "When did you leave?"
    assert follow_up.shown_when == Is("jsqData.Q1.a", ["Yes"])  # `not` `not` the option's key
    assert follow_up.required  # whenever shown
    assert fields["comments"].type is FieldType.TEXT and not fields["comments"].required
    assert fields["yearsOfExperience"].label == "Years of experience (a number)"


def test_a_sites_country_fields_are_kept_a_day_between_reads(opened, page, monkeypatch):
    _, fields = opened
    asked: list[dict] = []
    calls = _Config.calls

    def counted(self, payload, extras):
        asked.extend(extras)
        return calls(self, payload, extras)

    monkeypatch.setattr(_Config, "calls", counted)
    countries = lambda: [e for e in asked if "countryCode" in e]  # noqa: E731
    page.goto(JOB)
    assert [f.id for f in Phenom().browser_read(page)] == [f.id for f in fields]
    assert countries() == []
    monkeypatch.setattr(phenom, "COUNTRIES_KEPT_S", 0)
    page.goto(JOB)
    assert [f.id for f in Phenom().browser_read(page)] == [f.id for f in fields]
    assert countries()


def test_request_read_lists_the_same_fields(opened, monkeypatch):
    def open_(opener, request, timeout):
        if (body := served(request.full_url, request.data)) is None:
            raise OSError(f"404 from {request.full_url}")
        return io.BytesIO(body)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", open_)
    assert Phenom().request_read(JOB) == opened[1]


def answers(resume: str) -> dict:
    return {
        "resume": resume,
        "firstName": "Ada",
        "country": "United States",
        "cntryFields.city": "London",
        "state": "Utah",
        "phoneWidget.countryPhoneCode": "United States of America (+1)",
        "phoneWidget.phoneNumber": "+12025550143",
        "sourceType": "Referral",
        "applicantSource": "Employee",
        "nationality": "Canadian",
        "emailAgreement": "Yes",
        "smsOptIn": "No",
        "authorized": "Yes",
        "contactBy": ["SMS"],
        "startDate": "2027-02-14",
        "experienceData[0].title": "Analyst",
        "experienceData[0].fromTo.startDate": "03/2019",
        "educationData[0].schoolName": "Acme",
        "educationData[0].fromTo.endDate": "2004",
        "jsqData.Q1.a": "Yes",
        "jsqData.Q1.a_dot_a": "2020",
        "yearsOfExperience": "3",
        "comments": "Placeholder",
        "custAgreement": "Yes",
    }


def test_every_answer_reads_back_across_steps(opened, page, resume):
    adapter, fields = opened
    assert not matching.problems(fields, answers(resume))
    adapter.fill(page, answers(resume))
    assert "stepname=agreement" in page.url  # the last step, its submit left unclicked
    read_back = adapter.read_back(page)
    assert read_back["educationData[0].schoolName"] == "Acme University"
    checks = matching.check(fields, answers(resume), read_back)
    assert [c for c in checks if not c.match] == []
    shown = adapter.shown_fields(page)
    assert "jsqData.Q1.a_dot_a" in shown and not set(shown) - {f.id for f in fields}


def test_terms_the_wizard_opens_on_are_accepted_to_reach_the_form(opened, page, resume):
    adapter, fields = opened
    body = "<script>window.TERMS = true</script>" + (PAGES / "apply.html").read_text()
    page.route(APPLY, lambda route: route.fulfill(content_type="text/html", body=body))
    try:
        adapter.fill(page, answers(resume))
    finally:
        page.unroute(APPLY)
    checks = matching.check(fields, answers(resume), adapter.read_back(page))
    assert [c for c in checks if not c.match] == []


def test_the_next_answer_does_not_drop_the_country_the_page_takes_later(opened, page, resume):
    adapter, _ = opened
    given = answers(resume)
    del given["state"]  # its list would set the country again
    with pytest.raises(RuntimeError, match="step 1 did not pass") as passed:
        adapter.fill(page, given)
    assert str(passed.value).endswith("did not pass: state: This field is required")


def test_a_country_other_than_the_jobs_shows_its_own_fields(opened, page, resume):
    adapter, fields = opened
    given = answers(resume) | {
        "country": "Canada", "state": "Ontario", "cntryFields.region": "Alberta",
        "phoneWidget.countryPhoneCode": "Canada (+1)",
    }  # fmt: skip
    assert not matching.problems(fields, given)
    adapter.fill(page, given)
    checks = matching.check(fields, given, adapter.read_back(page))
    assert [c for c in checks if not c.match] == []
    assert "cntryFields.region" in adapter.shown_fields(page)


def test_a_step_the_wizard_turns_down_does_not_take_its_answers(opened, page, resume, monkeypatch):
    refused = {"status": "failure", "statusType": "VALIDATION_ERROR",
               "statusMessage": "Employee is not found for referral"}  # fmt: skip
    monkeypatch.setitem(REFERENCES, "applySubmit", {"applySubmit": {"response": refused}})
    with pytest.raises(NotTaken, match="^step 1: Employee is not found for referral$"):
        opened[0].fill(page, answers(resume))


def test_a_dependent_list_takes_the_options_its_parent_leaves(opened, resume):
    _, fields = opened
    under = lambda **given: [  # noqa: E731
        p for p in matching.problems(fields, answers(resume) | given) if p.split(":")[0] in given
    ]
    assert under(sourceType="Job Board", applicantSource="Employee") == [
        "applicantSource: 'Employee' is not one of ['LinkedIn', 'Indeed'], "
        'the options when "How did you hear about us?" is "Job Board"'
    ]
    assert under(country="Canada", state="Ontario") == []  # left to the read-back


def test_a_list_under_two_parents_keys_its_set_on_both():
    api = {"payload": '{"formData": {"applyddokey": "cities", "country": "%%formData.country%%",'
                      ' "region": "%%formData.region%%"}}'}  # fmt: skip
    schema = {
        "type": "object",
        "properties": {
            "country": {"title": "Country", "type": "string", "enum": ["US", "CA"],
                        "enumNames": ["United States", "Canada"], "default": "US"},
            "region": {"title": "Region", "type": "string", "enum": ["TX"], "enumNames": ["Texas"]},
            "city": {"title": "City", "type": "string"},
        },
    }  # fmt: skip
    city = {"htmlType": "asyncselect", "dependentKey": "region", "apiConfig": api}
    ui = {"country": {"ui:widget": "select"}, "region": {"ui:widget": "select"}, "city": city}
    posted = []

    def post(bodies):
        posted.extend(body["formData"] for body in bodies)
        return [{"applyGetReferences": {"data": {"values": [{"key": "1", "label": "Austin"}]}}}]

    reader = _Config({"locale": "en_us"}, {"schema": schema, "uiSchema": ui}, {}, post)
    raw = {r["id"]: r for r in reader.object(schema, ui, "", None, False, None)}["city"]
    assert posted[0] | {"country": "US", "region": "TX"} == posted[0]
    assert phenom._field(raw).option_sets == [
        OptionSet(AllOf([Is("region", ["Texas"]), Is("country", ["United States"])]), ["Austin"])
    ]


def test_a_source_table_keyed_by_source_is_one_option_set_per_source():
    schema = {
        "type": "object",
        "properties": {
            "source": {"title": "Source", "type": "string", "enum": ["web", "boards"],
                       "enumNames": ["Our Website", "Job Boards"]},
            "applicantSource": {"title": "Which one?", "type": "string"},
        },
    }  # fmt: skip
    table = {"web": [{"display": "Acme Careers", "value": "S-1"}],
             "boards": [{"display": "LinkedIn", "value": "S-2"}],
             "fairs": [{"display": "Career Fair", "value": "S-3"}]}  # fmt: skip
    flow = {
        "events": ["onchange"],
        "params": {"field": "source"},
        "workflows": [
            {"type": "temp_data", "data": {"config": [{"key": "jsonData", "data": table}]}},
            {"type": "transform",
             "data": {"config": [{"Path": "#schema.schema.properties.applicantSource"}]}},
        ],
    }  # fmt: skip
    ui = {"source": {"htmlType": "choice"}, "applicantSource": {"htmlType": "choice"}}
    config = {"schema": schema, "uiSchema": ui, "workflow": [flow]}
    reader = _Config({"locale": "en_us"}, config, {}, lambda bodies: [{}] * len(bodies))
    raw = {r["id"]: r for r in reader.object(schema, ui, "", None, False, None)}
    field = phenom._field(raw["applicantSource"])
    assert field.options is None and raw["applicantSource"]["parent"] == "source"
    assert field.option_sets == [  # a key the source list does not offer is no set
        OptionSet(Is("source", ["Our Website"]), ["Acme Careers"]),
        OptionSet(Is("source", ["Job Boards"]), ["LinkedIn"]),
    ]


def test_a_follow_up_its_trigger_hides_has_no_read_back(opened, page, resume):
    adapter, fields = opened
    given = answers(resume) | {"jsqData.Q1.a": "No"}
    adapter.fill(page, given)
    checks = matching.check(fields, given, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == [
        ("jsqData.Q1.a_dot_a", None)
    ]
    assert "jsqData.Q1.a_dot_a" not in adapter.shown_fields(page)


def test_an_apply_link_to_another_system_is_unsupported(page):
    page.route(
        JOB,
        lambda route: route.fulfill(
            content_type="text/html",
            body='<script>window.phApp = {}</script><a data-ph-at-id="apply-link"'
            ' href="https://acme.wd5.myworkdayjobs.com/External/job/1/apply">Apply</a>',
        ),
    )
    try:
        page.goto(JOB)
        with pytest.raises(Unsupported, match="external_step: .*myworkdayjobs"):
            Phenom().browser_read(page)
    finally:
        page.unroute(JOB)


def test_a_closed_posting_is_gone(page):
    gone = JOB.replace("1001", "1002")

    def closed(route):
        route.fulfill(status=404, content_type="text/html", body=(PAGES / "404.html").read_bytes())

    def at_gone(url: str) -> bool:
        return "ACMEUS1002" in url

    page.route(at_gone, closed)
    try:
        page.goto(gone)
        adapter = Phenom()
        assert adapter.detect(page.url, page)
        with pytest.raises(Unsupported, match="posting_gone"):
            adapter.browser_read(page)
    finally:
        page.unroute(at_gone)


def test_a_first_step_other_than_the_form_is_a_known_gap(page, monkeypatch):
    monkeypatch.setattr(phenom, "FORM_TIMEOUT_MS", 500)
    body = "<script>history.replaceState({}, '', location.href + '&stepname=login')</script>"
    page.route(APPLY, lambda route: route.fulfill(content_type="text/html", body=body))
    try:
        page.goto(APPLY)
        with pytest.raises(Unsupported, match="unsupported_flow: first_step: .*'login'"):
            Phenom().browser_read(page)
    finally:
        page.unroute(APPLY)


def test_rules_on_answers_become_conditions():
    schema = {
        "type": "object",
        "properties": {
            "visa": {"title": "Visa?", "type": "string", "enum": ["v1", "v2"],
                     "enumNames": ["Yes", "No"]},
            "box": {"title": "Relocate?", "type": "boolean"},
            "phone": {"title": "Phone", "type": "string"},
            "locale": {"type": "string"},
            "jobCountry": {"type": "string"},
            "jobLocation": {"type": "string"},
        },
        "allOf": [
            {"if": {"anyOf": [{"properties": {"visa": {"const": "v1"}}},
                              {"properties": {"box": {"const": True}}}]},
             "then": {"properties": {"which": {"title": "Which?", "type": "string"}},
                      "required": ["which"]}},
            {"if": {"properties": {"visa": {"enum": ["v2"]}}}, "then": {"required": ["phone"]}},
            {"if": {"properties": {"resumeName": {"isNotEmpty": True}}},
             "then": {"properties": {"cover": {"title": "Cover note", "type": "string"}}}},
            {"if": {"properties": {"locale": {"const": "fr_fr"}}},
             "then": {"properties": {"fr": {"title": "French", "type": "string"}}}},
            {"if": {"properties": {"jobCountry": {"const": "United States of America"}}},
             "then": {"properties": {"veteran": {"title": "Veteran?", "type": "string"}}}},
            {"if": {"properties": {"jobLocation": {"pattern": "Antrim"}}},
             "then": {"properties": {"religion": {"title": "Religion?", "type": "string"}}}},
        ],
    }  # fmt: skip
    ui = {"visa": {"ui:widget": "select"}, "box": {"ui:widget": "checkbox"},
          "locale": {"htmlType": "hidden"}, "jobCountry": {"htmlType": "hidden"},
          "jobLocation": {"htmlType": "hidden"}}  # fmt: skip
    config = {"schema": schema, "uiSchema": ui}
    job = {
        "response": {
            "response": {
                "jobCountry": "United States of America",
                "jobLocation": "Wilmington, Delaware",
            }
        }
    }
    ph = {"locale": "en_us", "ddo": job}
    reader = _Config(ph, config, {}, lambda bodies: [None] * len(bodies))
    raws = reader.object(schema, ui, "", None, False, None)
    for raw in raws:
        for key in ("when", "required_when"):
            raw[key] = reader.labeled(raw.get(key), resume=True)
    by_id = {r["id"]: r for r in raws}
    assert "fr" not in by_id  # on the page's own data
    assert by_id["veteran"]["when"] is None  # the job's, a hidden field takes from its data
    assert "religion" not in by_id  # a location the job's does not match
    assert by_id["which"]["when"] == AnyOf([Is("visa", ["Yes"]), Is("box", ["Yes"])])
    assert by_id["which"]["required"]
    assert by_id["phone"]["required_when"] == Is("visa", ["No"])
    assert by_id["cover"]["when"] == Is("resume")  # a resume given
    assert reader.labeled(Is("resume"), resume=False) is False  # no upload: never given
