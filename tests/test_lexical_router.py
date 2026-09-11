import pytest
from vad.decision.lexical_router import BM25Router, SkillDescriptor


@pytest.fixture
def sample_corpus():
    return [
        SkillDescriptor(
            id="skill-auth",
            name="Authentication Specialist",
            description="Handles OAuth2, JWT tokens, user sessions, and login security.",
            keywords=["oauth", "jwt", "token", "login", "password", "auth"],
            domain="security",
            target_agent="auth-dev-agent",
        ),
        SkillDescriptor(
            id="skill-db",
            name="Database Engineer",
            description="Manages SQL migrations, PostgreSQL schema, queries, and connection pools.",
            keywords=["sql", "postgres", "migration", "schema", "database", "query"],
            domain="data",
            target_agent="db-specialist-agent",
        ),
        SkillDescriptor(
            id="skill-telemetry",
            name="Observability Engineer",
            description="Configures OpenTelemetry metrics, Prometheus alerts, and Grafana dashboards.",
            keywords=["opentelemetry", "metrics", "prometheus", "grafana", "dashboard", "slo"],
            domain="telemetry",
            target_agent="obs-engineer-agent",
        ),
    ]


def test_lexical_router_accurate_routing(sample_corpus):
    router = BM25Router()
    router.index(sample_corpus)

    # Clear match on auth
    result = router.route("Help me debug an invalid JWT token in user login")
    assert result.match_id == "skill-auth"
    assert result.target_agent == "auth-dev-agent"
    assert result.domain == "security"
    assert result.is_confident is True

    # Clear match on database
    result_db = router.route("Need a PostgreSQL schema migration for user accounts")
    assert result_db.match_id == "skill-db"
    assert result_db.target_agent == "db-specialist-agent"
    assert result_db.is_confident is True


def test_lexical_router_empty_or_unrelated_query(sample_corpus):
    router = BM25Router()
    router.index(sample_corpus)

    result = router.route("")
    assert result.is_confident is False
    assert result.match_id is None

    result_unrelated = router.route("Make me a cheese sandwich with pickles")
    assert result_unrelated.is_confident is False


def test_lexical_router_top_k(sample_corpus):
    router = BM25Router()
    router.index(sample_corpus)

    results = router.route_top_k("Monitor database metrics and query performance", k=2)
    assert len(results) >= 2
    matched_ids = [r.match_id for r in results]
    assert "skill-db" in matched_ids or "skill-telemetry" in matched_ids
