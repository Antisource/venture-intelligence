"""Reduced public-schema regressions: no live requests or expected identities."""

import asyncio
import html
import json
from datetime import datetime, timezone

import pytest
from venture_intelligence.connectors.directory import DirectoryConnector
from venture_intelligence.connectors.triangulation import TriangulationConnector
from venture_intelligence.crawler import CrawlResult
from venture_intelligence.operations.cycle import IntelligenceCycleService
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    Company,
    EntityType,
    EventType,
    Source,
)
from venture_intelligence.storage.db import Database


def document(company, container):
    if container == "inertia":
        data = html.escape(json.dumps({"props": {"company": company}}), quote=True)
        return f'<div data-page="{data}"></div>'
    return (
        '<script id="__NEXT_DATA__">'
        + json.dumps({"props": {"pageProps": {"company": company}}})
        + "</script>"
    )


def test_public_json_collection_preserves_company_and_attributed_founder() -> None:
    url = "https://directory.example/api/companies?cohort=SR007"
    payload = json.dumps(
        {
            "count": 1,
            "results": [
                {
                    "name": "Cedar Labs",
                    "cohort": "SR007",
                    "website_url": "https://cedar.example",
                    "description": "Public company profile",
                    "founder_set": [
                        {
                            "first_name": "Jane",
                            "last_name": "Cedar",
                            "title": "CEO & Co-Founder",
                            "linkedin_url": "https://www.linkedin.com/in/jane-cedar/",
                            "private_email": "must-not-survive",
                        },
                        {
                            "first_name": "Sam",
                            "last_name": "Cedar",
                            "title": "CTO",
                            "linkedin_url": "https://www.linkedin.com/in/sam-cedar/",
                        },
                    ],
                }
            ],
        }
    )
    program = get_program_registry().get("speedrun_current").model_copy(
        update={"directory_url": url}
    )

    class Crawler:
        async def fetch_text(self, target):
            assert target == url
            return CrawlResult(url, payload, 200)

    connector = DirectoryConnector(ProgramRegistry([program]), Crawler())
    events = asyncio.run(connector.scan())
    repeated = asyncio.run(connector.scan())
    assert [event.event_type for event in events] == [
        EventType.COMPANY_LISTED,
        EventType.FOUNDER_DETECTED,
        EventType.FOUNDER_DETECTED,
    ]
    assert events[0].raw_payload["name"] == "Cedar Labs"
    assert events[0].raw_payload["founders"] == [
        {
            "first_name": "Jane",
            "last_name": "Cedar",
            "title": "CEO & Co-Founder",
            "linkedin_url": "https://www.linkedin.com/in/jane-cedar/",
            "_claim_container": "founder_set",
        },
        {
            "first_name": "Sam",
            "last_name": "Cedar",
            "title": "CTO",
            "linkedin_url": "https://www.linkedin.com/in/sam-cedar/",
            "_claim_container": "founder_set",
        },
    ]
    assert [event.raw_payload["name"] for event in events[1:]] == ["Jane Cedar", "Sam Cedar"]
    assert all(event.raw_payload["company_name"] == "Cedar Labs" for event in events[1:])
    assert [event.event_id for event in repeated] == [event.event_id for event in events]


def test_public_search_index_recovers_current_cohort_and_founders(tmp_path) -> None:
    """The official directory's public index is discovery transport, not fixture state."""
    program = get_program_registry().get("yc_current")
    assert program is not None
    assert program.directory_search_index == "YCCompany_production"
    assert program.directory_detail_url_template == "https://www.ycombinator.com/companies/{slug}"
    collection = program.directory_url
    assert collection is not None
    companies = {
        "forward": ("Forward", "Daniel Yoon"),
        "sinter": ("Sinter", "Alex Stone"),
        "onepatch": ("OnePatch", "Jamie Patch"),
    }

    class PublicIndexCrawler:
        async def fetch_text(self, url):
            if url == collection:
                return CrawlResult(
                    url,
                    '<script>window.AlgoliaOpts = {"app":"fixture-app",'
                    '"key":"public-search-key"};</script>',
                    200,
                )
            slug = url.rsplit("/", 1)[-1]
            name, founder = companies[slug]
            return CrawlResult(
                url,
                document(
                    {
                        "name": name,
                        "batch": "F26",
                        "ycdc_url": url,
                        "website": f"https://{slug}.example",
                        "founders": [
                            {
                                "full_name": founder,
                                "title": "CEO & Co-Founder",
                                "linkedin_url": f"https://linkedin.com/in/{slug}",
                            }
                        ],
                    },
                    "inertia",
                ),
                200,
            )

        async def post_json_text(self, url, payload, **kwargs):
            assert url == "https://fixture-app-dsn.algolia.net/1/indexes/YCCompany_production/query"
            assert "batch%3AFall+2026" in payload["params"]
            assert kwargs["headers"]["x-algolia-application-id"] == "fixture-app"
            return CrawlResult(
                url,
                json.dumps(
                    {
                        "hits": [
                            {
                                "name": name,
                                "batch": "Fall 2026",
                                "slug": slug,
                                "ycdc_url": None,
                            }
                            for slug, (name, _founder) in companies.items()
                        ]
                    }
                ),
                200,
            )

    db = Database(tmp_path / "f26.db")

    async def run() -> list[CanonicalEvent]:
        events = await DirectoryConnector(
            ProgramRegistry([program]), PublicIndexCrawler()
        ).scan()
        bus = EventBus(db)
        assert all(bus.publish(event) for event in events)
        await IntelligenceCycleService(database=db).process_canonical_events()
        return events

    events = asyncio.run(run())
    company_events = [item for item in events if item.entity_type is EntityType.COMPANY]
    founder_events = [item for item in events if item.entity_type is EntityType.PERSON]
    assert {item.raw_payload["name"] for item in company_events} == {
        "Forward",
        "Sinter",
        "OnePatch",
    }
    assert {item.raw_payload["batch"] for item in company_events} == {"F26"}
    assert len({item.entity_id for item in company_events}) == 3
    assert len(founder_events) == 3
    assert {item.batch for item in db.all_companies()} == {"F26"}
    assert {item.properties["program_id"] for item in db.all_companies()} == {"yc_current"}


def test_shared_collection_url_does_not_merge_distinct_companies(tmp_path) -> None:
    """A collection URL is acquisition provenance, not a company identity key."""
    db = Database(tmp_path / "intel.db")
    resolver = EntityResolver(db)
    source_url = "https://directory.example/api/companies?cohort=SR007"

    def company(source_id: str, name: str) -> CanonicalEvent:
        return CanonicalEvent(
            event_type=EventType.COMPANY_LISTED,
            entity_type=EntityType.COMPANY,
            entity_id=source_id,
            source=Source.DIRECTORY,
            source_url=source_url,
            occurred_at=datetime.now(timezone.utc),
            confidence=0.75,
            raw_payload={"name": name, "batch": "SR007", "program_id": "speedrun_current"},
            metadata={
                "program_id": "speedrun_current",
                "batch": "SR007",
                "identity_evidence": "explicit_public_company_record",
            },
        )

    cedar = resolver.resolve(company("directory__cedar", "Cedar Labs"))
    spruce = resolver.resolve(company("directory__spruce", "Spruce Labs"))

    assert cedar != spruce
    assert {item.canonical_name for item in db.all_companies()} == {"Cedar Labs", "Spruce Labs"}


def test_triangulation_never_retypes_a_person_as_a_company() -> None:
    program = get_program_registry().get("speedrun_current")

    class Crawler:
        async def fetch_text(self, url):
            return CrawlResult(url, "Nathan Roll joined the program", 200)

    connector = TriangulationConnector(ProgramRegistry([program]), Crawler())
    person = CanonicalEvent(
        event_type=EventType.FOUNDER_DETECTED,
        entity_type=EntityType.PERSON,
        entity_id="directory_person__nathan",
        source=Source.DIRECTORY,
        source_url="https://directory.example/oruk",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={"name": "Nathan Roll"},
        metadata={"program_id": "speedrun_current"},
    )

    assert not connector.can_investigate(person)
    assert asyncio.run(connector.investigate(person)) == []


def test_legacy_cross_entity_projection_is_hidden_without_deleting_events(tmp_path) -> None:
    db = Database(tmp_path / "intel.db")
    canonical_id = "legacy-motherboard"
    db.write_company(Company(company_id=canonical_id, canonical_name="Motherboard"))
    db.write_entity_validation(canonical_id, "company", evidence_count=1)
    source_event = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="directory__oruk",
        source=Source.DIRECTORY,
        source_url="https://directory.example/api/companies?cohort=SR007",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={"name": "Oruk", "batch": "SR007"},
        metadata={"identity_evidence": "explicit_public_company_record"},
    )
    db.write_event(source_event)
    db.write_entity_link(canonical_id, "company", "directory", source_event.entity_id, 0.75)

    assert not db.is_entity_validated(canonical_id)
    assert db.search_validated_entities("Motherboard") == []
    assert db.get_events_for_entity(source_event.entity_id) == [source_event]


@pytest.mark.parametrize(
    "container,program_id,batch",
    [
        ("inertia", "yc_current", "S26"),
        ("next", "speedrun_current", "SR006"),
    ],
)
def test_structured_directory_through_canonical_pipeline(tmp_path, container, program_id, batch):
    url = "https://directory.example/companies/cedar-labs"
    company = {
        "name": "Cedar Labs",
        "batch" if container == "inertia" else "cohort": batch,
        "website": "https://cedar.example",
        "description": "Public company profile",
        "founders": [{"full_name": "Jane Cedar", "title": "Founder", "has_email": True}],
    }
    program = (
        get_program_registry()
        .get(program_id)
        .model_copy(
            update={
                "directory_url": url,
                "batch": batch,
                "current_batch": batch,
            }
        )
    )

    class Crawler:
        async def fetch_text(self, target):
            assert target == url
            return CrawlResult(url, document(company, container), 200)

    db = Database(tmp_path / "intel.db")

    async def run():
        events = await DirectoryConnector(ProgramRegistry([program]), Crawler()).scan()
        assert len(events) == 1
        assert events[0].source_url == url
        assert events[0].raw_payload["founders"] == [
            {
                "full_name": "Jane Cedar",
                "title": "Founder",
                "_claim_container": "founders",
            }
        ]
        assert EventBus(db).publish(events[0])
        await IntelligenceCycleService(database=db).process_canonical_events()

    asyncio.run(run())
    companies = db.all_companies()
    assert len(companies) == 1
    assert companies[0].batch == batch  # SR006 must not silently become SR007.
    assert companies[0].canonical_name == "Cedar Labs"
    assert db.all_persons() == []  # A preserved founder claim is not a merge/promotion.


@pytest.mark.parametrize(
    "company",
    [
        {"name": "twitter", "batch": "S26"},
        {"name": "Cedar Labs", "batch": "S26", "ycdc_url": "https://other.example/company"},
    ],
)
def test_invalid_structured_record_cannot_fall_back_to_navigation(company):
    doc = document(company, "inertia") + '<a href="/companies/unrelated">Unrelated Labs</a>'
    program = (
        get_program_registry()
        .get("yc_current")
        .model_copy(
            update={
                "directory_url": "https://directory.example/companies/cedar",
                "batch": "S26",
            }
        )
    )

    class Crawler:
        async def fetch_text(self, url):
            return CrawlResult(url, doc, 200)

    assert asyncio.run(DirectoryConnector(ProgramRegistry([program]), Crawler()).scan()) == []


def test_other_cohort_does_not_become_current_membership():
    program = get_program_registry().get("yc_current")

    class Crawler:
        async def fetch_text(self, url):
            return CrawlResult(
                url, document({"name": "Cedar Labs", "batch": "S26"}, "inertia"), 200
            )

    assert asyncio.run(DirectoryConnector(ProgramRegistry([program]), Crawler()).scan()) == []


@pytest.mark.parametrize("program_id,batch", [("yc_current", "S26"), ("speedrun_current", "SR006")])
def test_attributed_founders_reach_evidence_research_and_relationships(tmp_path, program_id, batch):
    from venture_intelligence.intelligence.service import IntelligenceService

    company = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id=f"{program_id}__cedar",
        source=Source.DIRECTORY,
        source_url="https://directory.example/companies/cedar",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={
            "name": "Cedar Labs",
            "batch": batch,
            "program_id": program_id,
            "website": "https://cedar.example",
            "founders": [
                {
                    "full_name": "Jane Cedar",
                    "title": "CEO & Co-Founder",
                    "linkedin_url": "https://linkedin.com/in/jane-cedar",
                },
                {
                    "full_name": "Sam Cedar",
                    "title": "CTO",
                    "linkedin_url": "https://linkedin.com/in/sam-cedar",
                },
                {"full_name": "Taylor Cedar", "title": "Founder"},
            ],
        },
        metadata={
            "program_id": program_id,
            "batch": batch,
            "identity_evidence": "explicit_public_company_record",
        },
    )
    before = company.model_dump_json()
    founders = DirectoryConnector.founder_events(company)
    assert company.model_dump_json() == before
    assert len(founders) == 1  # Bare names / CTO titles do not prove a founder identity.
    assert founders == DirectoryConnector.founder_events(company)
    db = Database(tmp_path / "intel.db")
    for event in [company, *founders]:
        assert EventBus(db).publish(event)
    asyncio.run(IntelligenceCycleService(database=db).process_canonical_events())
    canonical = db.all_companies()[0]
    person = db.all_persons()[0]
    assert canonical.batch == person.batch == batch
    assert canonical.website == "https://cedar.example"
    assert canonical.founder_ids == [person.person_id]
    view = IntelligenceService(db).entity(canonical.company_id)
    assert view.entity.founder_ids == [person.person_id]
    assert view.evidence[0].source_urls == [company.source_url]
    assert IntelligenceService(db).entity(person.person_id).evidence
    context = IntelligenceService(db).analyst_entities(
        "Why does Cedar Labs belong to this program?"
    )
    assert len(context) == 2
    assert context[0]["entity"]["batch_id"] == batch
    assert context[0]["evidence"][0]["source_urls"] == [company.source_url]
    assert context[1]["entity"]["canonical_name"] == person.canonical_name
    assert context[1]["evidence"][0]["source_urls"] == [company.source_url]
    assert IntelligenceService(db).analyst_entities("Why does Cedar belong?") == []
    details = db.get_evidence_details_for_entity(canonical.company_id)
    assert batch in details[0]["observations"][0]["fact"]
    from venture_intelligence.agents.analyst import LocalBoundaryProvider

    answer = asyncio.run(
        LocalBoundaryProvider().answer(
            "Why this program?",
            {
                "entities": context,
                "citations": [{"label": "Official record", "url": company.source_url}],
            },
        )
    )
    assert answer.evidence_sufficient and batch in answer.answer
    assert "latest cohort" in answer.answer
    with db._connect() as connection:
        edge = connection.execute("SELECT * FROM graph_edges WHERE edge_type='FOUNDED'").fetchone()
        assert edge["source_id"] == person.person_id
        assert edge["target_id"] == canonical.company_id
        assert founders[0].event_id in json.loads(edge["evidence_ids"])


def test_common_names_and_usernames_have_no_merge_authority(tmp_path):
    db = Database(tmp_path / "intel.db")
    resolver = EntityResolver(db)

    def event(key, name, profile):
        return CanonicalEvent(
            event_type=EventType.FOUNDER_DETECTED,
            entity_type=EntityType.PERSON,
            entity_id=key,
            source=Source.RESEARCH_AGENT,
            source_url="https://company.example/team",
            occurred_at=datetime.now(timezone.utc),
            raw_payload={"name": name, "github_username": "janedoe", "profile_url": profile},
            metadata={
                "identity_validated": True,
                "identity_evidence": "public biographies",
                "supporting_source_count": 2,
                "candidate_hypothesis_id": key,
                "attribution_status": "attached",
                "identity_evidence_urls": ["https://company.example/team"],
            },
        )

    first = resolver.resolve(event("one", "Jane Doe", "https://profiles.example/jane-one"))
    second = resolver.resolve(event("two", "Jane Doe", "https://profiles.example/jane-two"))
    third = resolver.resolve(event("three", "Jane Doen", "https://profiles.example/jane-one"))
    assert len({first, second, third}) == 3
    assert resolver.resolve(event("four", "Jane Doe", "https://profiles.example/jane-one")) == first


def test_official_api_enrichment_requires_existing_identity_and_domain(tmp_path):
    from venture_intelligence.config import Settings
    from venture_intelligence.connectors.public_sources import GitHubConnector, ProductHuntConnector
    from venture_intelligence.intelligence.service import IntelligenceService

    company = CanonicalEvent(
        event_type=EventType.COMPANY_WEBSITE_FOUND,
        entity_type=EntityType.COMPANY,
        entity_id="website__cedar",
        source=Source.WEBSITE,
        source_url="https://cedar.example",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={"name": "Cedar Labs", "website": "https://cedar.example"},
        metadata={"investigation_depth": 1, "product_hunt_post_id": "42"},
    )

    class PublicApi:
        async def post_json_text(self, url, payload, **kwargs):
            return CrawlResult(
                url,
                json.dumps(
                    {
                        "data": {
                            "post": {
                                "name": "Cedar Labs",
                                "url": "https://www.producthunt.com/posts/cedar",
                                "website": "https://cedar.example",
                                "makers": [],
                            }
                        }
                    }
                ),
                200,
            )

        async def fetch_text(self, url):
            return CrawlResult(
                url,
                json.dumps(
                    {
                        "items": [
                            {
                                "full_name": "cedar/app",
                                "private": False,
                                "html_url": "https://github.com/cedar/app",
                                "homepage": "https://cedar.example",
                            },
                            {
                                "full_name": "imposter/app",
                                "private": False,
                                "html_url": "https://github.com/imposter/app",
                                "homepage": "https://other.example",
                            },
                            {
                                "full_name": "private/app",
                                "private": True,
                                "html_url": "https://github.com/private/app",
                                "homepage": "https://cedar.example",
                            },
                        ]
                    }
                ),
                200,
            )

    db = Database(tmp_path / "intel.db")
    bus = EventBus(db)
    runtime = Settings(
        _env_file=None,
        github_token=None,
        product_hunt_api_enabled=True,
        product_hunt_access_token="fixture-secret",
    )
    ph = ProductHuntConnector(get_program_registry(), PublicApi(), runtime_settings=runtime)
    launch = asyncio.run(ph.investigate(company))[0]
    assert not bus.publish(launch)  # An API post can never create a company identity.
    assert bus.publish(company)
    service = IntelligenceCycleService(database=db)
    asyncio.run(service.process_canonical_events())
    assert bus.publish(launch)
    assert launch.metadata["triggered_by"] == company.event_id
    assert launch.metadata["investigation_depth"] == 2
    bad = launch.model_copy(deep=True)
    bad.raw_payload["product_hunt_post"]["resolved_website"] = "https://imposter.example"
    assert not bus.publish(bad)
    gh = GitHubConnector(get_program_registry(), PublicApi(), runtime_settings=runtime)
    repositories = asyncio.run(gh.investigate(company))
    assert len(repositories) == 2  # No private repository evidence.
    for event in repositories:
        assert bus.publish(event)
    asyncio.run(service.process_canonical_events())
    canonical = db.all_companies()[0]
    view = IntelligenceService(db).entity(canonical.company_id)
    urls = {url for item in view.evidence for url in item.source_urls}
    assert "https://github.com/cedar/app" in urls
    assert "https://www.producthunt.com/posts/cedar" in urls
    assert "https://github.com/imposter/app" not in urls
    assert not db.all_persons()
    assert canonical.batch is None and view.entity.program_id is None
    with db._connect() as connection:
        assert not connection.execute(
            "SELECT 1 FROM graph_edges WHERE edge_type='ACCEPTED_BY'"
        ).fetchone()
    assert "fixture-secret" not in launch.model_dump_json()


def test_github_rejects_non_json_search_text():
    from venture_intelligence.config import Settings
    from venture_intelligence.connectors.public_sources import GitHubConnector

    class InvalidApi:
        async def fetch_text(self, url):
            return CrawlResult(url, "Cedar Labs public search result", 200)

    trigger = CanonicalEvent(
        event_type=EventType.COMPANY_WEBSITE_FOUND,
        entity_type=EntityType.COMPANY,
        entity_id="website__cedar",
        source=Source.WEBSITE,
        source_url="https://cedar.example",
        occurred_at=datetime.now(timezone.utc),
        raw_payload={"name": "Cedar Labs"},
    )
    connector = GitHubConnector(
        get_program_registry(),
        InvalidApi(),
        runtime_settings=Settings(_env_file=None, github_token=None),
    )
    assert asyncio.run(connector.investigate(trigger)) == []


def test_cli_has_one_cycle_and_one_readiness_job():
    from venture_intelligence.cli import _parser

    parser = _parser()
    assert parser.parse_args(["cycle", "run"]).command == "cycle"
    assert parser.parse_args(["doctor"]).command == "doctor"
    for removed in (["status"], ["discovery", "run"]):
        with pytest.raises(SystemExit) as failure:
            parser.parse_args(removed)
        assert failure.value.code == 2


def test_doctor_human_output_is_concise_but_json_preserves_diagnostics(capsys):
    from venture_intelligence.cli import _emit

    result = {
        "readiness": {
            "components": [{"component": "Database", "status": "READY", "summary": "Accessible."}],
            "provider_policies": [{"implementation_details": "system-only"}],
        },
        "analyst": {"status": "HEALTHY", "provider": "local-boundary"},
    }
    _emit(result, as_json=False)
    output = capsys.readouterr().out
    assert "Database: READY" in output and "HEALTHY" in output
    assert "provider_policies" not in output and "system-only" not in output
    _emit(result, as_json=True)
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize(
    "answer",
    [
        "Async belongs to Y Combinator's 'Current' batch.",
        "Async is recorded in S26 and belongs to the latest cohort.",
    ],
)
def test_analyst_rejects_live_observed_historical_cohort_relabeling(answer):
    from venture_intelligence.agents.analyst import AnalystAgent, AnalystResult
    from venture_intelligence.config import Settings

    class LocalModel:
        name = "local-test"

        async def answer(self, question, context):
            return AnalystResult(answer=answer, model=self.name, evidence_sufficient=True)

    context = {
        "entities": [
            {
                "entity": {
                    "canonical_name": "Async",
                    "program_id": "yc_current",
                    "batch_id": "S26",
                },
                "evidence": [{"claim": "Official directory records S26 participation"}],
            }
        ],
        "citations": [
            {"label": "Official listing", "url": "https://www.ycombinator.com/companies/withasync"}
        ],
    }
    agent = AnalystAgent(Settings(_env_file=None, analyst_provider="local"), provider=LocalModel())
    result = asyncio.run(agent.answer("What is Async's recorded cohort?", context))
    assert result.model == "local-boundary"
    assert "cohort S26" in result.answer
    assert any("discarded" in caveat for caveat in result.caveats)
    assert result.citations == context["citations"]
    assert agent._grounded_result(
        AnalystResult(answer="Async has recorded cohort S26.", model="local-test"), context
    )
