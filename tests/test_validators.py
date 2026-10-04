import copy
import json
from pathlib import Path

import pytest

from copilot_harness.config import TestsConfig
from copilot_harness.models import RequirementsDoc, TestPlan
from copilot_harness.validators import validate_requirements, validate_test_plan

from .notes_project import PLAN, REQUIREMENTS


@pytest.fixture
def cfg() -> TestsConfig:
    return TestsConfig(min_total=4, max_total=20, min_functional_ratio=0.3,
                       min_per_category={"performance": 1, "security": 1, "usability": 1}, delta_min=1, delta_max=5)


def write(tmp_path: Path, name: str, data) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return p


def test_valid_requirements(tmp_path):
    doc, issues = validate_requirements(write(tmp_path, "r.json", REQUIREMENTS))
    assert issues == [] and doc is not None


def test_requirements_errors(tmp_path):
    bad = copy.deepcopy(REQUIREMENTS)
    bad["requirements"][1]["id"] = "REQ-001"
    bad["requirements"][2]["thresholds"] = []
    bad["requirements"][3]["type"] = "vibes"
    _, issues = validate_requirements(write(tmp_path, "r.json", bad))
    assert any("type" in i for i in issues)
    bad["requirements"][3]["type"] = "usability"
    _, issues = validate_requirements(write(tmp_path, "r.json", bad))
    assert any("used 2 times" in i for i in issues)
    assert any("no measurable thresholds" in i for i in issues)


def test_requirements_not_json(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{oops")
    assert "not valid JSON" in validate_requirements(p)[1][0]


def test_requirements_are_append_only(tmp_path):
    previous = RequirementsDoc.model_validate(REQUIREMENTS)
    edited = copy.deepcopy(REQUIREMENTS)
    edited["requirements"][0]["title"] = "Something else"
    edited["requirements"].append({"id": "REQ-005", "title": "New", "type": "functional", "priority": "P1",
                                   "description": "desc", "acceptance_criteria": ["a"], "origin": "WRONG"})
    _, issues = validate_requirements(write(tmp_path, "r.json", edited), previous, "CHG-001")
    assert any("REQ-001 was edited" in i for i in issues)
    assert any('origin to "CHG-001"' in i for i in issues)


def test_valid_plan(tmp_path, cfg):
    reqs = RequirementsDoc.model_validate(REQUIREMENTS)
    plan, issues = validate_test_plan(write(tmp_path, "p.json", PLAN), reqs, cfg)
    assert issues == [] and plan is not None


def test_plan_distribution_and_coverage(tmp_path, cfg):
    reqs = RequirementsDoc.model_validate(REQUIREMENTS)
    plan = copy.deepcopy(PLAN)
    plan["tests"] = [t for t in plan["tests"] if t["id"] not in ("FUNC-002", "SEC-001")]
    plan["tests"][0]["id"] = "PERF-009"  # prefix does not match category
    plan["tests"][-1].pop("threshold", None)
    _, issues = validate_test_plan(write(tmp_path, "p.json", plan), reqs, cfg)
    text = "\n".join(issues)
    assert "requires prefix FUNC-" in text
    assert "only 0 security tests" in text
    assert "REQ-002 (Security headers) has no tests" in text
    assert "REQ-001 is P0 and needs at least one negative" in text


def test_plan_append_only(tmp_path, cfg):
    reqs = RequirementsDoc.model_validate(REQUIREMENTS)
    previous = TestPlan.model_validate(PLAN)
    plan = copy.deepcopy(PLAN)
    plan["tests"][0]["expected"] = ["weaker"]
    plan["tests"].pop(1)
    _, issues = validate_test_plan(write(tmp_path, "p.json", plan), reqs, cfg, previous, "CHG-001")
    text = "\n".join(issues)
    assert "FUNC-001 was edited" in text
    assert "FUNC-002 was removed" in text
    assert "adds 0 tests" in text


def test_plan_rejects_tests_on_superseded_requirements(tmp_path, cfg):
    reqs_data = copy.deepcopy(REQUIREMENTS)
    reqs_data["requirements"][1].update(status="superseded", superseded_by=["REQ-004"])
    reqs = RequirementsDoc.model_validate(reqs_data)
    _, issues = validate_test_plan(write(tmp_path, "p.json", PLAN), reqs, cfg)
    assert any("SEC-001 is active but traces to superseded requirement REQ-002" in i for i in issues)


def test_delta_cannot_retire_all_tests_of_an_active_requirement(tmp_path, cfg):
    reqs_data = copy.deepcopy(REQUIREMENTS)
    reqs_data["requirements"].append({"id": "REQ-005", "title": "Delete", "type": "functional", "priority": "P1",
                                      "description": "desc", "acceptance_criteria": ["a"], "origin": "CHG-001"})
    reqs = RequirementsDoc.model_validate(reqs_data)
    previous = TestPlan.model_validate(PLAN)
    plan = copy.deepcopy(PLAN)
    for t in plan["tests"]:
        if t["id"] == "SEC-001":
            t["status"] = "retired"  # REQ-002 is still active
    plan["tests"].append({"id": "FUNC-004", "title": "deletes", "category": "functional", "group": "del",
                          "priority": "P1", "req_ids": ["REQ-005"], "steps": ["s"], "expected": ["e"], "origin": "CHG-001"})
    _, issues = validate_test_plan(write(tmp_path, "p.json", plan), reqs, cfg, previous, "CHG-001")
    assert any("REQ-002 (Security headers) has no tests" in i for i in issues)
