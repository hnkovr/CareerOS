"""Deterministic extractors: JSON-LD JobPosting, embedded app state, readable text."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
)
from careeros.modules.platform.fetch.extract.embedded import (
    find_next_data,
    find_nuxt,
    find_rsc_chunks,
    search_keys,
)
from careeros.modules.platform.fetch.extract.jsonld import (
    find_jobposting,
    iter_jsonld,
    jobposting_to_posting,
)
from careeros.modules.platform.fetch.extract.text import (
    html_meta,
    html_to_text,
    markdown_body,
    markdown_title,
    text_to_posting,
)
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
URL = "https://careers.northwind.example/jobs/senior-data-engineer-4711"


def _html(name: str) -> str:
    return (FIXTURES / "generic" / name).read_text()


# --------------------------------------------------------------------------- JSON-LD


def test_find_jobposting_walks_graph_and_lists() -> None:
    node = find_jobposting(_html("jobposting.html"))
    assert node is not None and node["title"] == "Senior Data Engineer"
    plain = '<script type="application/ld+json">{"@type":"JobPosting","title":"X"}</script>'
    assert (find_jobposting(plain) or {}).get("title") == "X"
    listed = (
        '<script type="application/ld+json">[{"@type":"WebPage"},'
        '{"@type":["Thing","JobPosting"],"title":"Y"}]</script>'
    )
    assert (find_jobposting(listed) or {}).get("title") == "Y"
    assert find_jobposting('<script type="application/ld+json">{broken</script>') is None
    assert find_jobposting("<p>no ld</p>") is None
    assert find_jobposting({"@graph": [{"@type": "JobPosting", "title": "Z"}]}) == {
        "@type": "JobPosting",
        "title": "Z",
    }
    assert len(list(iter_jsonld(_html("jobposting.html")))) == 1


def test_jobposting_to_posting_maps_fields_and_evidence() -> None:
    node = find_jobposting(_html("jobposting.html"))
    assert node is not None
    posting = jobposting_to_posting(node, Platform.website, URL, fetched_at=NOW)
    assert posting.title == "Senior Data Engineer" and posting.company == "Northwind Commerce"
    assert posting.external_id == "NW-4711" and posting.location == "Warsaw, PL"
    assert posting.published_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert posting.expires_at == datetime(2026, 10, 1, tzinfo=UTC)
    ex = posting.extraction
    assert ex is not None
    assert ex.employment_type == EmploymentType.full_time and ex.contract_type is None
    assert ex.remote_policy == RemotePolicy.remote_region and ex.remote_regions == ["Poland"]
    assert ex.technologies == ["Python", "SQL", "dbt", "Dagster", "ClickHouse"]
    assert ex.requirements == ["5+ years of data engineering", "Production dbt experience"]
    assert ex.deadline == date(2026, 10, 1)
    assert ex.compensation is not None
    assert (ex.compensation.min, ex.compensation.max) == (25000.0, 32000.0)
    assert ex.compensation.currency == "PLN" and ex.compensation.period == CompensationPeriod.month
    # escaped HTML description → readable text, kept in raw_text after the header lines
    assert "<p>" not in posting.raw_text and "&lt;" not in posting.raw_text
    assert "- Design ELT pipelines with dbt and Dagster" in posting.raw_text
    assert posting.raw_text.startswith("Senior Data Engineer\nNorthwind Commerce\nWarsaw, PL")
    assert posting.raw_payload is not None and posting.raw_payload["direct_apply"] is True
    fields = {e.field: e for e in posting.field_evidence}
    assert fields["title"].source == "jsonld" and fields["title"].source_url == URL
    assert fields["compensation"].value["min"] == 25000.0 and fields["title"].observed_at == NOW
    assert "external_id" in fields and "expires_at" in fields


def test_jobposting_salary_and_employment_variants() -> None:
    base = {"@type": "JobPosting", "title": "T", "hiringOrganization": "Acme Example"}
    single = jobposting_to_posting(
        {**base, "baseSalary": {"currency": "usd", "value": 120000, "unitText": "YEAR"}},
        Platform.website,
        URL,
    )
    comp = single.extraction.compensation  # type: ignore[union-attr]
    assert comp is not None and (comp.min, comp.max, comp.currency) == (120000.0, 120000.0, "USD")
    assert comp.period == CompensationPeriod.year

    listed = jobposting_to_posting(
        {**base, "baseSalary": [{"currency": "EUR", "minValue": "4 500", "maxValue": "6,000"}]},
        Platform.website,
        URL,
    )
    comp = listed.extraction.compensation  # type: ignore[union-attr]
    assert comp is not None and (comp.min, comp.max, comp.period) == (4500.0, 6000.0, None)

    nothing = jobposting_to_posting(
        {**base, "baseSalary": {"currency": "EUR"}}, Platform.website, URL
    )
    assert nothing.extraction.compensation is None  # type: ignore[union-attr]

    contractor = jobposting_to_posting(
        {
            **base,
            "employmentType": ["CONTRACTOR", "PART_TIME"],
            "description": "Plain **markdown**",
        },
        Platform.website,
        URL,
    )
    assert contractor.extraction.contract_type == ContractType.b2b  # type: ignore[union-attr]
    assert contractor.extraction.employment_type == EmploymentType.part_time  # type: ignore[union-attr]
    assert contractor.raw_text.endswith("Plain **markdown**")  # markdown kept verbatim
    assert contractor.company == "Acme Example" and contractor.location is None


def test_jobposting_without_title_is_not_a_job() -> None:
    import pytest

    with pytest.raises(ValueError):
        jobposting_to_posting({"@type": "JobPosting", "description": "x"}, Platform.website, URL)


# --------------------------------------------------------------------------- embedded state


def test_find_next_data_and_search_keys() -> None:
    data = find_next_data(_html("nextdata.html"))
    assert data is not None and data["page"] == "/jobs/[id]"
    found = search_keys(data, ("title", "grade", "englishLevel", "salary_estimated", "missing"))
    assert found == {
        "title": "Data Engineer",
        "grade": "senior",
        "englishLevel": "B2",
        "salary_estimated": True,
    }
    assert find_next_data("<html></html>") is None


def test_find_rsc_chunks_unescapes_and_search_keys_reads_them() -> None:
    chunks = find_rsc_chunks(_html("rsc.html"))
    assert len(chunks) == 2 and chunks[0].startswith('3:["$","div"')
    found = search_keys(chunks, ("grade", "workFormat", "avgSalary", "original", "key_skills_en"))
    assert found["grade"] == "senior" and found["workFormat"] == "remote"
    assert found["avgSalary"] == 4200 and found["original"] is None
    assert found["key_skills_en"] == ["Python", "dbt"]
    # still-escaped form straight from the HTML also works (best effort)
    raw_found = search_keys(_html("rsc.html"), ("englishLevel", "salary_estimated"))
    assert raw_found == {"englishLevel": "B2", "salary_estimated": True}
    assert find_rsc_chunks("<p>nothing</p>") == []


def test_find_nuxt_and_tolerance() -> None:
    data = find_nuxt(_html("nuxt.html"))
    assert isinstance(data, dict) and data["serverRendered"] is True
    assert search_keys(data, ("currency", "from")) == {"currency": "PLN", "from": 18000}
    assert find_nuxt("<script>window.__NUXT__=(function(a){return {a:a}}(1));</script>") is None
    assert search_keys(None, ("x",)) == {} and search_keys({"a": 1}, ()) == {}
    assert search_keys('{"broken": [1, 2', ("broken",)) == {}


# --------------------------------------------------------------------------- readable text


def test_html_to_text_drops_chrome_and_keeps_structure() -> None:
    text = html_to_text(_html("jobposting.html"))
    assert "dataLayer" not in text and ".hero" not in text  # script / style dropped
    assert "Log in" not in text and "Privacy" not in text  # nav / footer dropped
    assert "Senior Data Engineer" in text
    assert "- Design ELT pipelines with dbt and Dagster" in text  # list bullets kept
    assert "\n\n\n" not in text
    assert html_to_text("&lt;p&gt;escaped &amp; html&lt;/p&gt;") == "escaped & html"
    assert html_to_text("") == ""


def test_html_meta_reads_title_h1_og_and_canonical() -> None:
    meta = html_meta(_html("jobposting.html"))
    assert meta["title"] == "Senior Data Engineer – Northwind Commerce Careers"
    assert meta["h1"] == "Senior Data Engineer" and meta["og:site_name"] == "Northwind Commerce"
    assert meta["canonical"] == URL and meta["lang"] == "en"


def test_markdown_body_splits_jina_header() -> None:
    md = (FIXTURES / "jina" / "reader.md").read_text()
    meta, body = markdown_body(md)
    assert meta == {
        "title": "Senior Data Engineer – Northwind Commerce Careers",
        "url": URL,
        "published": "2026-08-20T09:00:00Z",
    }
    assert (
        body.startswith("# Senior Data Engineer") and markdown_title(body) == "Senior Data Engineer"
    )
    plain_meta, plain_body = markdown_body("# Just markdown\n\ntext")
    assert plain_meta == {} and plain_body.startswith("# Just markdown")


def test_text_to_posting_keeps_text_and_stated_fields_only() -> None:
    import pytest

    text = html_to_text(_html("og_only.html"))
    posting = text_to_posting(
        text,
        Platform.website,
        URL,
        title="Analytics Engineer (Remote, EU)",
        company="Lumen Analytics",
        fetched_at=NOW,
        source="og_meta",
    )
    assert (
        posting.title == "Analytics Engineer (Remote, EU)" and posting.company == "Lumen Analytics"
    )
    assert posting.raw_text == text and "EUR 70 000" in posting.raw_text  # heuristics run at ingest
    assert posting.extraction is not None and posting.extraction.compensation is None
    assert {(e.field, e.source) for e in posting.field_evidence} == {
        ("title", "og_meta"),
        ("company", "og_meta"),
    }
    assert all(e.observed_at == NOW for e in posting.field_evidence)
    untitled = text_to_posting("First line becomes the title\nmore", Platform.website, URL)
    assert untitled.title == "First line becomes the title"
    with pytest.raises(ValueError):
        text_to_posting("   \n  ", Platform.website, URL)
