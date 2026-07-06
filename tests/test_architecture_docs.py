from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_distinguishes_local_level3_from_enterprise_level4():
    text = read("README.md")

    assert "Level 2 reference implementation with an offline Level 3 demonstrator" in text
    assert "does not deploy to live cloud or production infrastructure" in text
    assert "does not provide an enterprise-hosted Level 4 control plane" in text
    assert "additional live providers remain planned" in text


def test_reference_architecture_lists_implemented_and_directional_level3_scope():
    text = read("Reference Architecture.md")

    assert "Implemented local demonstrator characteristics" in text
    assert "`vad swarm run --fixture ... --workdir ...`" in text
    assert "`vad deploy failure-demo`" in text
    assert "Still directional beyond this repository" in text
    assert "does not provide production distributed orchestration" in text


def test_maturity_model_keeps_level4_future_and_level3_offline():
    text = read("maturity_model.md")

    assert "offline Level 3 demonstrator" in text
    assert "without live credentials or cloud services" in text
    assert "Level 4 remains a target architecture" in text
    assert "not an enterprise-hosted control plane" in text


def test_deployment_docs_do_not_claim_fake_provider_is_live_production():
    text = read("docs/deployment.md")

    assert "governed fake-provider lifecycle" in text
    assert "They are not live deployment commands" in text
    assert "Live deployment providers must add separate opt-in tests" in text


def test_control_plane_architecture_distinguishes_implemented_planned_and_future_cloud():
    text = read("docs/control-plane.md")

    for heading in [
        "## Implemented Local Level 4 Boundary",
        "## Remaining Local Hardening",
        "## Future Cloud Scope",
        "## Reference Architecture Delta Closure",
    ]:
        assert heading in text

    assert "coordinates multi-client identity, event replay, task leases, governed MCP visibility" in text
    assert "local apply/uninstall/rollback writers are implemented" in text
    assert "hosted VAD SaaS or managed tenancy" in text
    assert "Any future cloud or SaaS plan must be introduced by a later plan/tracker item" in text


def test_control_plane_architecture_does_not_claim_hosted_saas_completion():
    text = read("docs/control-plane.md")

    assert "without a cloud service" in text
    assert "not implemented in the current local reference architecture" in text
    assert "no hosted VAD SaaS" in text
    assert "no cloud-hosted MCP gateway" in text
    assert "managed plugin marketplace publication or automatic marketplace acceptance" in text
    assert "implemented hosted VAD SaaS" not in text
    assert "implemented cloud-hosted MCP gateway" not in text
    assert "marketplace acceptance is complete" not in text
