import asyncio
import json

import pytest

from app import agents, analysis, llm, targets


def outcome():
    return {"sent": 25, "acked": 25, "stored": 25, "lost": 0, "errors": 0, "error_pct": 0,
            "p95_ms": 100, "peak_p95_ms": 6000, "recovery_s": 3, "guard_tripped": True, "guard_reason": "latency"}


def sample(target="mysql", kind="crash"):
    return {"run_id": "one", "experiment": {"id": f"{kind}-{target}", "target": target, "type": kind,
             "title": f"{kind} {target}", "params": {}}, "outcome": outcome(), "notes": []}


def test_hawkbit_mysql_fix_does_not_reference_voting_app_or_redis():
    graph = targets.load("hawkbit").graph()
    fixes = analysis.rules_fixes([{"id": "F1", "experiment_id": "crash-mysql"}], [sample()], graph)
    snippets = "\n".join(f["snippet"] for f in fixes)
    assert "mysql" in snippets and "hawkbit" in snippets
    assert "vote/app.py" not in snippets and "Redis(" not in snippets


def test_rabbitmq_durability_fix_does_not_run_redis_server():
    r = sample("rabbitmq")
    r["outcome"]["lost"] = 2
    fixes = analysis.rules_fixes([{"id": "F1", "experiment_id": "crash-rabbitmq"}], [r], targets.load("hawkbit").graph())
    assert any("verify broker persistence" in f["title"] for f in fixes)
    assert all("redis-server" not in f["snippet"] for f in fixes)


@pytest.mark.parametrize("rows", [
    [{"id": "F1", "title": "Failure", "impact": "Impact"}, None],
    [{"id": "F1", "title": "Failure", "impact": "Impact"}] * 2,
    [{"id": ["F1"], "title": "Failure", "impact": "Impact"}],
    [{"id": "F1", "title": None, "impact": "Impact"}],
    [{"id": "F1", "title": "85% errors", "impact": "Impact"}],
])
def test_malformed_or_fabricated_findings_are_rejected_without_crashing(rows, monkeypatch):
    async def fake_complete(*args, **kwargs):
        assert not kwargs["accept"](json.dumps({"findings": rows}))
        raise llm.NoModelAvailable("rejected")
    monkeypatch.setattr(llm, "complete", fake_complete)
    _, source, _ = asyncio.run(analysis.findings([sample()], targets.load("hawkbit").graph(),
                                              targets.load("hawkbit").unit, "docker"))
    assert source == "rules"


@pytest.mark.parametrize("lost", [float("inf"), -1, 1.5, True, {}, []])
def test_invalid_prediction_counts_are_rejected(lost):
    d = {"expected_error_pct": 10, "expected_votes_lost": lost, "expected_recovery_s": 3,
         "confidence": "low", "reason": "hypothesis", "hypothesis": "not measured"}
    assert not agents._accept(json.dumps(d))


def test_validation_exceptions_reject_model_answers():
    def check(_):
        raise TypeError("bad model schema")
    assert not llm.accepted(check, "malformed")


def test_configuration_signature_changes_with_graph_rate_and_duration():
    graph = targets.load("hawkbit").graph()
    signature = agents.configuration_signature(graph, 2, 10)
    assert signature != agents.configuration_signature(graph, 5, 10)
    assert signature != agents.configuration_signature(graph, 2, 5)
    changed = json.loads(json.dumps(graph))
    changed["nodes"][0]["restart"] = "unless-stopped"
    assert signature != agents.configuration_signature(changed, 2, 10)


def test_restart_policy_does_not_claim_to_recover_paused_mysql():
    graph = targets.load("hawkbit").graph()
    graph["nodes"] = [{**n, "restart": "always"} for n in graph["nodes"]]
    prediction = agents.rules_prediction(sample(kind="hang")["experiment"], graph, entry="hawkbit",
                                         sync={"hawkbit", "mysql"}, rate=2, fault_s=10, history=[], unit_plural="devices")
    assert "it restarts by itself" not in prediction["reason"]
