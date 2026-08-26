"""RocketHunt: URL shapes, JSON-LD + embedded-state extraction, the contact gate, the read chain.

Everything runs offline against sanitised fixtures (``fixtures/rockethunt/``); the transports
assert on **every** request, so a connector that ever touched ``/api/`` — or any URL other than
the canonical page and ``robots.txt`` — fails the suite instead of the site's terms.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from careeros.core.config import Settings
from careeros.modules.opportunities.enums import RemotePolicy, Seniority
from careeros.modules.platform.base import ConnectorContext
from careeros.modules.platform.connectors.generic.connector import Connector as GenericConnector
from careeros.modules.platform.connectors.rockethunt import extract, urls
from careeros.modules.platform.connectors.rockethunt.connector import SAMPLE_URL, Connector
from careeros.modules.platform.enums import (
    AccessMode,
    AuthKind,
    FetchStrategy,
    SourceRelation,
    SyncMethod,
)
from careeros.modules.platform.fetch.artifact import FetchArtifact, JobReadError
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.fetch.cache import FetchCache
from careeros.modules.platform.fetch.robots import RobotsPolicy, reset_robots_cache
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.platform.schemas import DoctorCheck, JobQuery
from careeros.modules.platform.sources import SourceKind, SourceRef, detect
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "rockethunt"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

EN_UUID = "7d3f1c2a-9b64-4e21-8f0a-1c2d3e4f5a6b"
RU_UUID = "2c9a4f18-7e35-4b90-9a71-6d5e8b3c2f04"
ORIG_UUID = "51e8b0d7-3a2c-4f6b-b8d1-0a7c9e4f21b3"
EN_URL = f"https://rockethunt.ai/en/vacancies/{EN_UUID}"
RU_URL = f"https://rockethunt.ai/ru/vacancies/{RU_UUID}"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text()


class Site:
    """A RocketHunt that answers exactly two paths and shouts about anything else."""

    def __init__(self, page: str = "", *, status: int = 200) -> None:
        self.page = page or _fx("vacancy_en.html")
        self.status = status
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        assert request.url.host in ("rockethunt.ai", "www.rockethunt.ai"), (
            f"unexpected host: {request.url}"
        )
        assert not path.startswith("/api/"), f"the connector must never call /api/: {request.url}"
        if path == "/robots.txt":
            return httpx.Response(200, text=_fx("robots.txt"))
        assert path.startswith("/en/vacancies/") or path.startswith("/ru/vacancies/"), (
            f"only a vacancy page may be requested, got {request.url}"
        )
        if self.status >= 400:
            return httpx.Response(
                self.status, text=_fx("not_found.html"), headers={"content-type": "text/html"}
            )
        return httpx.Response(self.status, text=self.page, headers={"content-type": "text/html"})


def _ctx(settings: Settings, site: Site, **overrides: object) -> ConnectorContext:
    s = settings.model_copy(update=dict(overrides))
    return ConnectorContext(
        settings=s, http=build_http(s, transport=httpx.MockTransport(site)), now=NOW
    )


def _policy(ctx: ConnectorContext) -> RobotsPolicy:
    return RobotsPolicy(ctx.http, user_agent=ctx.settings.platform_user_agent, cache={})


def _artifact(name: str, url: str, *, external_id: str | None = None) -> FetchArtifact:
    return FetchArtifact(
        provider=Platform.rockethunt,
        strategy=FetchStrategy.public_html,
        requested_url=url,
        resolved_url=url,
        external_id=external_id,
        fetched_at=NOW,
        status_code=200,
        content_type="text/html",
        raw_text=_fx(name) if name.endswith(".html") else name,
    )


@pytest.fixture(autouse=True)
def _no_robots_cache() -> None:
    reset_robots_cache()


# ------------------------------------------------------------------ capabilities / registry


def test_capabilities_declare_a_public_read_one_provider() -> None:
    caps = Connector().capabilities
    assert caps.platform == Platform.rockethunt
    assert caps.read_job == [FetchStrategy.public_html, FetchStrategy.jina, FetchStrategy.wayback]
    assert caps.access == AccessMode.public and caps.auth == AuthKind.none
    assert caps.official_api is False and caps.email_fallback is False
    assert caps.jobs == [SyncMethod.paste] and caps.profile == [] and caps.applications == []
    assert caps.read_one is True and caps.manual_capture is True
    assert "aggregator estimate" in caps.notes and "contacts gated" in caps.notes
    assert PlatformRegistry([Connector()]).verify() == []


def test_detection_beats_the_generic_fallback() -> None:
    registry = PlatformRegistry([Connector(), GenericConnector()])
    hit = detect(EN_URL, registry)
    assert hit is not None and hit.platform == Platform.rockethunt and hit.confidence == 0.95
    other = detect("https://rockethunt.ai/en/faq", registry)
    assert other is not None and other.platform == Platform.website  # generic keeps it at 0.1


# ------------------------------------------------------------------------------ URL shapes


@pytest.mark.parametrize(
    "url",
    [
        EN_URL,
        RU_URL.replace(RU_UUID, EN_UUID),
        f"{EN_URL}?utm_source=telegram&ref=share",
        f"{EN_URL}#description",
        f"{EN_URL}/",
        f"https://www.rockethunt.ai/en/vacancies/{EN_UUID.upper()}",
    ],
)
def test_detect_canonicalises_every_vacancy_url_form(url: str) -> None:
    hit = Connector().detect(url)
    assert hit is not None
    assert hit.canonical.canonical_url == EN_URL  # always the en page of the same uuid
    assert hit.canonical.external_id == EN_UUID and hit.canonical.host == "rockethunt.ai"
    assert hit.canonical.locale in ("en", "ru")


def test_detect_keeps_the_locale_the_user_gave() -> None:
    assert Connector().detect(RU_URL) is not None
    ru = Connector().detect(RU_URL)
    assert ru is not None and ru.canonical.locale == "ru"
    assert ru.canonical.canonical_url == f"https://rockethunt.ai/en/vacancies/{RU_UUID}"


@pytest.mark.parametrize(
    "url",
    [
        "https://rockethunt.ai/en",
        "https://rockethunt.ai/en/vacancies",
        "https://rockethunt.ai/en/vacancies/not-a-uuid",
        f"https://rockethunt.ai/de/vacancies/{EN_UUID}",
        f"https://rockethunt.ai/en/profiles/{EN_UUID}",
        f"https://rockethunt.ai/api/vacancies/{EN_UUID}",
        f"https://rockethunt.example/en/vacancies/{EN_UUID}",
        "https://rockethunt.ai/en/vacancies/7d3f1c2a-9b64-1e21-8f0a-1c2d3e4f5a6b",  # not v4
        f"mailto:jobs@rockethunt.ai?body={EN_UUID}",
        "/en/vacancies/" + EN_UUID,
    ],
)
def test_detect_refuses_everything_that_is_not_a_vacancy_page(url: str) -> None:
    assert Connector().detect(url) is None


def test_canonicalize_from_a_reference_marks_private_sources() -> None:
    c = Connector()
    src = c.canonicalize(f"{EN_URL}?utm_campaign=x")
    assert src.canonical_url == EN_URL and src.external_id == EN_UUID and src.private is False
    ref = SourceRef(kind=SourceKind.telegram_message, value=f"look: {RU_URL} 👀")
    private = c.canonicalize(ref)
    assert private.canonical_url == f"https://rockethunt.ai/en/vacancies/{RU_UUID}"
    assert private.private is True and private.locale == "ru"
    with pytest.raises(ValueError, match="not a vacancy URL"):
        c.canonicalize("https://rockethunt.ai/en/faq")
    with pytest.raises(ValueError, match="without a URL"):
        c.canonicalize(SourceRef(kind=SourceKind.text, value="no link here"))


# ------------------------------------------------------------------------------ extraction


def test_jsonld_fields_are_mapped() -> None:
    posting = Connector().extract_job(_artifact("vacancy_en.html", EN_URL))
    assert posting.title == "Head of Partner Sales" and posting.company == "Acme Analytics"
    assert posting.location == "Lisbon, Portugal" and posting.external_id == EN_UUID
    assert posting.published_at == datetime(2026, 7, 14, 9, 12, 3, tzinfo=UTC)
    assert posting.expires_at == datetime(2026, 9, 30, tzinfo=UTC)
    assert posting.raw_text.startswith("Head of Partner Sales\nAcme Analytics\nLisbon, Portugal")
    assert "# Head of Partner Sales" in posting.raw_text  # description markdown kept verbatim

    got = posting.extraction
    assert got is not None
    salary = got.compensation
    assert salary is not None
    assert (salary.min, salary.max, salary.currency) == (4200.0, 6800.0, "EUR")
    assert str(salary.period) == "month" and salary.type == "salary"
    assert got.employment_type is not None and str(got.employment_type) == "full_time"
    assert got.deadline == datetime(2026, 9, 30, tzinfo=UTC).date()
    assert got.technologies[:4] == [
        "B2B Sales",
        "Partnerships",
        "Negotiation",
        "Pipeline management",
    ]
    # "Head, English: B2" → a seniority and a language requirement, not two opaque strings
    assert got.seniority == Seniority.lead
    assert got.requirements[0] == "English: B2"
    assert not any(r.strip() == "Head" for r in got.requirements)
    assert got.summary is not None and got.summary.startswith("Acme Analytics builds")
    assert got.responsibilities[0].startswith("Build the partner channel from zero")


def test_embedded_state_adds_what_the_jsonld_omits() -> None:
    posting = Connector().extract_job(_artifact("vacancy_en.html", EN_URL))
    got = posting.extraction
    assert got is not None
    assert got.remote_policy == RemotePolicy.hybrid  # work_formats[].kind = hybrid
    assert "Experience: 5–8 years" in got.requirements
    assert "Relocation: Portugal, Spain" in got.requirements
    assert "HubSpot" in got.technologies  # key_skills_en extends the JSON-LD skills

    embedded = (posting.raw_payload or {})["embedded"]
    assert embedded["grade"] == "Head" and embedded["english_level"] == "B2"
    assert embedded["company_type"] == "product"
    assert embedded["company_website"] == "https://acme-analytics.example"
    assert embedded["industry"] == "Analytics" and embedded["specialization"] == "Sales & Bizdev"
    assert embedded["published_at"] == "2026-07-14T09:12:03Z"
    assert embedded["updated_at"] == "2026-08-21T22:33:24Z"

    by_field = {e.field: e for e in posting.field_evidence if e.source == "embedded"}
    assert by_field["seniority"].value == "lead" and by_field["remote_policy"].value == "hybrid"
    assert {"industry", "specialization", "company_type", "company_website"} <= set(by_field)


def test_the_i18n_label_dictionary_is_never_mistaken_for_data() -> None:
    """The page ships ``"grade": "Grade"`` … before the record; anchoring on the uuid saves us."""
    html = _fx("vacancy_en.html")
    assert r"\"grade\":\"Grade\"" in html and r"\"englishLevel\":\"English level\"" in html
    record = extract.find_vacancy_record(html, EN_UUID)
    assert record is not None and record["id"] == EN_UUID
    embedded = extract.read_embedded(record)
    assert embedded["grade"] == "Head" and embedded["english_level"] == "B2"
    assert embedded["experience_min_years"] == 5  # not the "Experience from" label


def test_similar_vacancies_do_not_leak_into_the_read_one() -> None:
    posting = Connector().extract_job(_artifact("vacancy_en.html", EN_URL))
    embedded = (posting.raw_payload or {})["embedded"]
    assert embedded["grade"] != "Middle" and embedded["experience_min_years"] == 5
    assert "similar_vacancies" not in embedded


def test_salary_without_a_figure_in_the_text_is_an_aggregator_estimate() -> None:
    posting = Connector().extract_job(_artifact("vacancy_en.html", EN_URL))
    assert (posting.raw_payload or {})["salary_is_estimate"] is True
    got = posting.extraction
    assert got is not None and got.compensation is not None
    assert got.compensation.raw is not None
    assert got.compensation.raw.startswith("RocketHunt estimate (source_type=country")
    evidence = [e for e in posting.field_evidence if e.field == "compensation"]
    assert [(e.source, e.confidence) for e in evidence] == [
        ("jsonld", 0.9),
        ("aggregator_estimate", 0.4),
    ]
    # the estimate's own basis is not the vacancy's source — they must stay apart
    assert (posting.raw_payload or {})["embedded"]["salary_analytics"]["source_type"] == "country"
    assert (posting.raw_payload or {})["source_hint"] == {
        "source": "telegram",
        "source_name": "Acme Jobs Channel",
        "source_type": None,
    }


def test_ru_page_parses_the_same_way_and_keeps_a_salary_the_text_states() -> None:
    posting = Connector().extract_job(_artifact("vacancy_ru.html", RU_URL))
    assert posting.title == "Аналитик данных" and posting.external_id == RU_UUID
    got = posting.extraction
    assert got is not None
    assert got.seniority == Seniority.mid and got.remote_policy == RemotePolicy.remote_global
    assert got.technologies == ["SQL", "Python", "dbt", "Продуктовая аналитика"]  # key_skills_ru
    assert "English: B1" in got.requirements and "Experience: 3+ years" in got.requirements
    assert "Вилка: 250 000 – 400 000 ₽ на руки" in got.requirements
    assert "salary_is_estimate" not in (posting.raw_payload or {})
    assert got.compensation is not None and got.compensation.raw is not None
    assert not got.compensation.raw.startswith("RocketHunt estimate")
    evidence = [e for e in posting.field_evidence if e.field == "compensation"]
    assert [(e.source, e.confidence) for e in evidence] == [("jsonld", 0.9), ("board_page", 0.9)]


def test_a_public_original_becomes_a_link_and_an_aggregates_relation() -> None:
    posting = Connector().extract_job(
        _artifact("vacancy_with_original.html", f"https://rockethunt.ai/en/vacancies/{ORIG_UUID}")
    )
    assert (
        posting.original_url == "https://careers.acme-analytics.example/jobs/head-of-partner-sales"
    )
    assert posting.relation == SourceRelation.aggregates
    assert (posting.raw_payload or {})["closed"] is True  # archived on RocketHunt
    assert (posting.raw_payload or {})["source_hint"]["source_type"] == "employer_page"


def test_a_rendered_original_block_is_not_a_link() -> None:
    """``original`` normally holds the original-language body (a React node), never a URL."""
    posting = Connector().extract_job(_artifact("vacancy_en.html", EN_URL))
    assert posting.original_url is None and posting.relation == SourceRelation.primary


# ------------------------------------------------------------------------------ contact gate


def test_the_contact_gate_never_becomes_content() -> None:
    html = _fx("vacancy_en.html")
    assert "Show contacts" in html  # the page really does carry the paid gate
    posting = Connector().extract_job(_artifact("vacancy_en.html", EN_URL))
    assert "Show contacts" not in posting.raw_text
    assert posting.extraction is not None and posting.extraction.recruiter is None
    payload = posting.raw_payload or {}
    assert not any("contact" in key.lower() for key in payload["embedded"])
    assert not any("contact" in e.field.lower() for e in posting.field_evidence)


def test_the_contact_gate_is_stripped_from_the_text_fallback_too() -> None:
    """No JSON-LD → readable text; the gate's button and hint must not survive that path."""
    import re

    stripped = re.sub(
        r"<script type=\"application/ld\+json\">.*?</script>",
        "",
        _fx("vacancy_en.html"),
        flags=re.S,
    )
    posting = Connector().extract_job(_artifact(stripped, EN_URL))
    assert posting.title == "Head of Partner Sales"
    assert "Show contacts" not in posting.raw_text
    assert "Reach out directly about this role" not in posting.raw_text
    assert "Build the partner channel from zero" in posting.raw_text  # the vacancy body survives
    assert posting.extraction is not None and posting.extraction.recruiter is None


# ------------------------------------------------------------------------------ the read chain


async def test_read_requests_only_robots_and_the_canonical_page(settings: Settings) -> None:
    site = Site()
    ctx = _ctx(settings, site)
    c = Connector()
    source = c.canonicalize(f"{EN_URL}?utm_source=share#apply")
    read = await c.fetch_job(ctx, source, FetchBudget(), cache=FetchCache(), policy=_policy(ctx))
    assert site.paths == ["/robots.txt", f"/en/vacancies/{EN_UUID}"]
    posting = read.posting
    assert posting is not None
    assert posting.canonical_url == EN_URL and posting.strategy == FetchStrategy.public_html
    assert posting.fetched_at == NOW and posting.content_hash and posting.fingerprint
    assert posting.quality == 1.0 and posting.completeness == 1.0 and not posting.is_archive
    assert [a.strategy for a in read.attempts] == [FetchStrategy.public_html]

    request = posting.to_ingest()
    assert request.source == "rockethunt" and request.url == EN_URL
    assert request.external_id == EN_UUID
    assert request.raw_payload is not None
    assert request.raw_payload["provenance"]["strategy"] == "public_html"
    assert request.received_at == datetime(2026, 7, 14, 9, 12, 3, tzinfo=UTC)


async def test_read_of_a_dead_vacancy_reports_every_attempt(settings: Settings) -> None:
    site = Site(status=404)
    ctx = _ctx(
        settings,
        site,
        job_fetch_enable_jina=False,
        job_fetch_enable_wayback=False,
    )
    c = Connector()
    with pytest.raises(JobReadError) as exc:
        await c.fetch_job(
            ctx, c.canonicalize(EN_URL), FetchBudget(), use_cache=False, policy=_policy(ctx)
        )
    assert [a.error_type for a in exc.value.attempts] == ["not_found"]
    assert (
        "jina: disabled" in exc.value.diagnostics and "wayback: disabled" in exc.value.diagnostics
    )
    assert site.paths == ["/robots.txt", f"/en/vacancies/{EN_UUID}"]


async def test_robots_disallow_stops_the_public_read(settings: Settings) -> None:
    class Blocked(Site):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                self.paths.append(request.url.path)
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            return super().__call__(request)

    site = Blocked()
    ctx = _ctx(settings, site, job_fetch_enable_jina=False, job_fetch_enable_wayback=False)
    c = Connector()
    with pytest.raises(JobReadError) as exc:
        await c.fetch_job(
            ctx, c.canonicalize(EN_URL), FetchBudget(), use_cache=False, policy=_policy(ctx)
        )
    assert [a.error_type for a in exc.value.attempts] == ["robots_disallow"]
    assert site.paths == ["/robots.txt"]  # the page itself was never requested


# ------------------------------------------------------------------------------ paste / links


def test_paste_reuses_the_shared_jobs_parser() -> None:
    jobs = Connector().parse_jobs_text(
        f"Head of Partner Sales at Acme Analytics\nHybrid, Lisbon\n{EN_URL}\n"
    )
    assert len(jobs) == 1 and jobs[0].platform == Platform.rockethunt
    assert jobs[0].title == "Head of Partner Sales" and jobs[0].url == EN_URL


def test_search_url_is_the_sites_own_deep_link() -> None:
    c = Connector()
    assert c.search_url(JobQuery(text="data engineer")) == (
        "https://rockethunt.ai/en?text=data+engineer"
    )
    assert (
        c.search_url(JobQuery(text="C++ & SQL")) == "https://rockethunt.ai/en?text=C%2B%2B+%26+SQL"
    )
    assert c.search_url(JobQuery(location="Berlin", remote=True)) is None
    assert c.profile_url() is None


# ------------------------------------------------------------------------------ doctor


def _by_name(checks: list[DoctorCheck]) -> dict[str, DoctorCheck]:
    return {c.name: c for c in checks}


async def test_doctor_reports_detection_robots_page_structure_and_the_gate(
    settings: Settings,
) -> None:
    site = Site()
    ctx = _ctx(settings, site)
    checks = await Connector().doctor(ctx)
    names = [c.name for c in checks]
    assert names == [
        "capabilities",
        "detection",
        "robots",
        "public_html",
        "structured_data",
        "original_source",
        "contacts",
    ]
    by_name = _by_name(checks)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert by_name["robots"].detail.startswith("rockethunt.ai: allow")
    assert by_name["public_html"].detail.startswith(f"GET {SAMPLE_URL} → 200")
    assert by_name["structured_data"].detail == "jsonld: JobPosting present"
    assert by_name["original_source"].detail == "absent (aggregated post only)"
    assert "never fetched" in by_name["contacts"].detail
    sample = urls.parse_vacancy(SAMPLE_URL)
    assert sample is not None
    assert site.paths == ["/robots.txt", f"/en/vacancies/{sample[0]}"]


async def test_doctor_stays_honest_when_the_sample_page_is_gone(settings: Settings) -> None:
    ctx = _ctx(settings, Site(status=404))
    by_name = _by_name(await Connector().doctor(ctx))
    assert by_name["public_html"].ok is True  # the host answered — that is what we probe for
    assert "belongs to no vacancy" in by_name["public_html"].detail
    assert "structured_data" not in by_name and by_name["contacts"].ok is True


async def test_doctor_does_not_read_a_page_robots_forbids(settings: Settings) -> None:
    class Blocked(Site):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            self.paths.append(request.url.path)
            assert request.url.path == "/robots.txt", "robots said no — nothing else may be read"
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    site = Blocked()
    checks = await Connector().doctor(_ctx(settings, site))
    by_name = _by_name(checks)
    assert by_name["robots"].ok is False and by_name["robots"].fix is not None
    assert "public_html" not in by_name and by_name["contacts"].ok is True
    assert site.paths == ["/robots.txt"]
