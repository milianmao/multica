"""business-quality-gate 的反假绿编排回归测试。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "business-quality-gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("business_quality_gate_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _arguments(project: Path, output: Path, run_id: str, **overrides):
    values = {
        "project_root": str(project),
        "sdd": "docs/v6.99.0/契约规范.md",
        "no_sdd": False,
        "kb": "docs/project-note/03-核心数据业务流",
        "target": ["src/service.py"],
        "changed_files": [],
        "output_dir": str(output),
        "run_id": run_id,
        "evidence_level": "schema-only",
        "verify_command": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "docs/v6.99.0").mkdir(parents=True)
    (project / "docs/project-note/03-核心数据业务流").mkdir(parents=True)
    (project / "src").mkdir()
    (project / "docs/v6.99.0/契约规范.md").write_text(
        "# v6.99.0 契约规范\n", encoding="utf-8"
    )
    (project / "docs/project-note/03-核心数据业务流/订单.md").write_text(
        "# 创建订单\n\n## 触发条件\n- 用户提交\n\n## 数据流\n- 创建订单\n\n## 异常处理\n- 库存不足\n",
        encoding="utf-8",
    )
    (project / "src/service.py").write_text("def create_order():\n    return 'ok'\n", encoding="utf-8")
    return project


def _install_stubbed_boundaries(monkeypatch, gate, *, write_evidence=None, pytest_failed=False):
    def run_command(command, cwd, *, redact_command=False, env=None):
        command = list(command)
        if str(gate.CONTRACT_EXTRACTOR) in command:
            output = Path(command[command.index("--output-dir") + 1])
            output.mkdir(parents=True)
            (output / "test_order_contract.py").write_text("def test_contract(): pass\n", encoding="utf-8")
            (output / "oracle-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "contract-oracle-v1",
                        "coverage": {"covered": 1, "total": 1, "ratio": 1.0},
                        "contracts": [{"status": "covered"}],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(command, 0, "{}", "")
        elif str(gate.SCENARIO_EXTRACTOR) in command:
            output = Path(command[command.index("--output-dir") + 1])
            output.mkdir(parents=True)
            (output / "test_order_scenario.py").write_text("def test_scenario(): pass\n", encoding="utf-8")
            (output / "scenario-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "scenario-oracle-v1",
                        "coverage": {"covered_flows": 1, "total_flows": 1, "ratio": 1.0},
                        "flows": [{"flow_name": "创建订单", "status": "covered"}],
                        "mock_ceiling_risk": False,
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(command, 0, "{}", "")
        elif str(gate.QUALITY_CHECK) in command:
            completed = subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"gate": {"verdict": "PASS", "blocking_findings": []}}),
                "",
            )
        else:
            if write_evidence is not None:
                write_evidence(env)
            completed = subprocess.CompletedProcess(command, 0, "{}", "")
        return completed, gate._command_record(command, completed, redact_command=redact_command)

    def run_pytest(paths, project_root, run_id):
        return {
            "schema_version": "pytest-results-v1",
            "run_id": run_id,
            "exit_code": 1 if pytest_failed else 0,
            "raw_exit_code": 1 if pytest_failed else 0,
            "summary": {"passed": 1, "failed": 1 if pytest_failed else 0, "skipped": 0},
            "collected_tests": [str(path) for path in paths],
        }

    monkeypatch.setattr(gate, "_run_command", run_command)
    monkeypatch.setattr(gate, "_run_pytest", run_pytest)


def _write_valid_evidence(env):
    assert env is not None
    Path(env["BUSINESS_QUALITY_VERIFY_EVIDENCE"]).write_text(
        json.dumps(
            {
                "schema_version": "business-verification-v1",
                "run_id": env["BUSINESS_QUALITY_RUN_ID"],
                "source_sdd_hash": env["BUSINESS_QUALITY_SOURCE_SDD_HASH"],
                "source_kb_hash": env["BUSINESS_QUALITY_SOURCE_KB_HASH"],
                "target_code_hash": env["BUSINESS_QUALITY_TARGET_CODE_HASH"],
                "assertions": [{"type": "state-transition", "passed": True}],
            }
        ),
        encoding="utf-8",
    )


def test_schema_only_stays_blocked_after_clean_generation(tmp_path, monkeypatch):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    _install_stubbed_boundaries(monkeypatch, gate)

    assert gate.run_gate(_arguments(project, output, "schema-only")) == gate.GATE_BLOCK_EXIT_CODE

    payload = json.loads((output / "schema-only/gate-result.json").read_text(encoding="utf-8"))
    assert payload["gate"]["verdict"] == "BLOCK"
    assert "schema_only_evidence" in payload["gate"]["reasons"]
    assert payload["steps"]["pytest"]["collected_tests"] == [
        str(output / "schema-only/contract/test_order_contract.py"),
        str(output / "schema-only/scenario/test_order_scenario.py"),
    ]


def test_code_verified_requires_bound_business_evidence(tmp_path, monkeypatch):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"

    def write_stale_evidence(env):
        _write_valid_evidence(env)
        path = Path(env["BUSINESS_QUALITY_VERIFY_EVIDENCE"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["target_code_hash"] = "sha256:stale"
        path.write_text(json.dumps(payload), encoding="utf-8")

    _install_stubbed_boundaries(monkeypatch, gate, write_evidence=write_stale_evidence)

    assert gate.run_gate(
        _arguments(
            project,
            output,
            "stale-evidence",
            evidence_level="code-verified",
            verify_command="verify-business",
        )
    ) == gate.GATE_BLOCK_EXIT_CODE

    payload = json.loads((output / "stale-evidence/gate-result.json").read_text(encoding="utf-8"))
    assert "real_verification_failed" in payload["gate"]["reasons"]
    assert payload["steps"]["real_verification"]["evidence_valid"] is False


def test_code_verified_passes_with_current_real_business_evidence(tmp_path, monkeypatch):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    _install_stubbed_boundaries(monkeypatch, gate, write_evidence=_write_valid_evidence)

    assert gate.run_gate(
        _arguments(
            project,
            output,
            "verified",
            evidence_level="code-verified",
            verify_command="verify-business",
        )
    ) == 0

    payload = json.loads((output / "verified/gate-result.json").read_text(encoding="utf-8"))
    assert payload["gate"]["verdict"] == "PASS"
    assert (output / "verified/business-verification.json").is_file()


def test_existing_run_id_is_rejected_before_evidence_reuse(tmp_path, monkeypatch):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    (output / "reused").mkdir(parents=True)
    _install_stubbed_boundaries(monkeypatch, gate)

    with pytest.raises(ValueError, match="already exists"):
        gate.run_gate(_arguments(project, output, "reused"))


def test_code_verified_rejects_shell_control_tokens_before_generation(tmp_path):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"

    with pytest.raises(ValueError, match="shell control tokens"):
        gate.run_gate(
            _arguments(
                project,
                output,
                "shell-token",
                evidence_level="code-verified",
                verify_command="verify-business && unexpected-command",
            )
        )

    assert not (output / "shell-token").exists()


def test_oracle_test_failure_blocks_even_with_real_evidence(tmp_path, monkeypatch):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    _install_stubbed_boundaries(
        monkeypatch,
        gate,
        write_evidence=_write_valid_evidence,
        pytest_failed=True,
    )

    assert gate.run_gate(
        _arguments(
            project,
            output,
            "pytest-failed",
            evidence_level="code-verified",
            verify_command="verify-business",
        )
    ) == gate.GATE_BLOCK_EXIT_CODE

    payload = json.loads((output / "pytest-failed/gate-result.json").read_text(encoding="utf-8"))
    assert "oracle_pytest_failed" in payload["gate"]["reasons"]


def test_no_sdd_passes_with_real_verification(tmp_path, monkeypatch):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    _install_stubbed_boundaries(monkeypatch, gate, write_evidence=_write_valid_evidence)

    assert gate.run_gate(
        _arguments(
            project,
            output,
            "no-sdd-pass",
            no_sdd=True,
            sdd=None,
            evidence_level="code-verified",
            verify_command="verify-business",
        )
    ) == 0

    payload = json.loads((output / "no-sdd-pass/gate-result.json").read_text(encoding="utf-8"))
    assert payload["gate"]["verdict"] == "PASS"
    assert payload["sdd_mode"] == "declared-none"
    assert payload["steps"]["contract_extract"]["declared_skipped"] is True
    contract = json.loads((output / "no-sdd-pass/contract/oracle-manifest.json").read_text(encoding="utf-8"))
    assert contract["declared_skipped"] is True
    assert contract["skip_reason"] == "no-cross-endpoint"


def test_no_sdd_requires_real_verification(tmp_path, monkeypatch):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    _install_stubbed_boundaries(monkeypatch, gate)

    with pytest.raises(ValueError, match="code-verified requires --verify-command"):
        gate.run_gate(
            _arguments(
                project,
                output,
                "no-sdd-block",
                no_sdd=True,
                sdd=None,
                evidence_level="code-verified",
            )
        )


def test_sdd_version_directory_mismatch_rejected(tmp_path):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    # H1 说 v6.99.0 但目录改成 v7.0.0 → 拒绝
    (project / "docs/v7.0.0").mkdir(parents=True)
    (project / "docs/v7.0.0/契约规范.md").write_text("# v6.99.0 契约规范\n", encoding="utf-8")

    with pytest.raises(ValueError, match="version directory mismatch"):
        gate.run_gate(_arguments(project, output, "mismatch", sdd="docs/v7.0.0/契约规范.md"))


def test_changed_business_file_not_in_target_rejected(tmp_path):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    (project / "src").mkdir(parents=True, exist_ok=True)
    (project / "src/api.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed business file not in --target"):
        gate.run_gate(
            _arguments(
                project,
                output,
                "changed-miss",
                changed_files=["src/api.py"],
            )
        )


def test_changed_test_file_not_in_target_is_allowed(tmp_path, monkeypatch):
    gate = _load_gate()
    project = _project(tmp_path)
    output = tmp_path / "evidence"
    _install_stubbed_boundaries(monkeypatch, gate)

    assert gate.run_gate(
        _arguments(
            project,
            output,
            "changed-test-only",
            changed_files=["tests/test_service.py"],
        )
    ) == gate.GATE_BLOCK_EXIT_CODE  # schema-only 仍 BLOCK，但不应因 changed 校验报错

    payload = json.loads((output / "changed-test-only/gate-result.json").read_text(encoding="utf-8"))
    assert "schema_only_evidence" in payload["gate"]["reasons"]
