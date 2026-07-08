"""Declarative acceptance policy: evidence strength x changed path.

A valid signature is not acceptance. The policy decides whether the VERIFIED
evidence in a receipt is strong enough for the paths the PR changes. Like the
trust anchor, `.agent-receipts/policy.yml` is read only from the base branch.

Semantics:

- Each changed path is matched against rules in order; the first rule whose
  patterns match wins (CODEOWNERS-style, but first-match not last-match).
- A rule's `require` lists the evidence methods that are acceptable for the
  paths it covers; the path passes if the receipt carries at least one
  GATE-VERIFIED evidence item of any listed method (OR semantics).
- `require: []` means no evidence-strength requirement for those paths — the
  receipt itself (signed, bound, trusted) is still required.
- Paths matching no rule fall back to `default_require`, which defaults to
  the strong classes. Unknown paths failing closed is the point.
- `self_claimed` is never a verified method, so listing it in `require` is a
  config error rather than a loophole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .receipt import EVIDENCE_METHODS

POLICY_PATH = ".agent-receipts/policy.yml"

STRONG_METHODS = ("re_executable", "ci_attested")
_REQUIRABLE = tuple(m for m in EVIDENCE_METHODS if m != "self_claimed")


class PolicyConfigError(ValueError):
    """policy.yml is malformed. The gate fails CLOSED on this."""


@dataclass
class PolicyRule:
    name: str
    patterns: list[str]
    require: list[str]
    _compiled: list[re.Pattern] = field(default_factory=list, repr=False)

    def matches(self, path: str) -> bool:
        if not self._compiled:
            self._compiled = [glob_to_regex(p) for p in self.patterns]
        return any(rx.match(path) for rx in self._compiled)


@dataclass
class PolicySettings:
    require_receipt: bool = True
    waiver_label: str = "human-waiver"
    require_request_binding: bool = False
    # Whether require_request_binding was set explicitly in the file. Its
    # default is the permissive value, so an armed gate refuses to run unless
    # the policy states it outright (see run_gate). Never itself a policy knob.
    require_request_binding_set: bool = False
    distrust_ci_when_workflows_change: bool = True
    re_executable_allowlist: list[str] = field(default_factory=list)


@dataclass
class Policy:
    settings: PolicySettings = field(default_factory=PolicySettings)
    default_require: list[str] = field(default_factory=lambda: list(STRONG_METHODS))
    rules: list[PolicyRule] = field(default_factory=list)

    @classmethod
    def default(cls) -> "Policy":
        return cls()


@dataclass
class PathFinding:
    path: str
    rule: str
    require: list[str]
    satisfied: bool


@dataclass
class PolicyDecision:
    passed: bool
    findings: list[PathFinding]

    @property
    def failed_paths(self) -> list[PathFinding]:
        return [f for f in self.findings if not f.satisfied]


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a gitignore-flavored glob to a regex over posix paths.

    Supported: `**` (any path segments, including none), `*` (within one
    segment), `?` (one char within a segment). A pattern ending in `/`
    matches everything under that directory.
    """
    if pattern.endswith("/"):
        pattern += "**"
    out: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 3] == "**/":
                out.append(r"(?:[^/]+/)*")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(r".*")
                i += 2
                continue
            out.append(r"[^/]*")
            i += 1
            continue
        if c == "?":
            out.append(r"[^/]")
            i += 1
            continue
        out.append(re.escape(c))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def parse_policy(text: str) -> Policy:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyConfigError(f"policy.yml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyConfigError("policy.yml must be a YAML mapping")
    if data.get("version") != 1:
        raise PolicyConfigError("policy.yml 'version' must be 1")

    policy = Policy()

    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise PolicyConfigError("policy.yml 'settings' must be a mapping")
    for key, value in settings.items():
        if key == "require_receipt":
            policy.settings.require_receipt = _require_bool(key, value)
        elif key == "waiver_label":
            if not isinstance(value, str) or not value:
                raise PolicyConfigError("settings.waiver_label must be a non-empty string")
            policy.settings.waiver_label = value
        elif key == "require_request_binding":
            policy.settings.require_request_binding = _require_bool(key, value)
            policy.settings.require_request_binding_set = True
        elif key == "distrust_ci_when_workflows_change":
            policy.settings.distrust_ci_when_workflows_change = _require_bool(key, value)
        elif key == "re_executable_allowlist":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise PolicyConfigError("settings.re_executable_allowlist must be a list of strings")
            policy.settings.re_executable_allowlist = list(value)
        else:
            raise PolicyConfigError(f"unknown settings key: {key}")

    if "default_require" in data:
        policy.default_require = _parse_require("default_require", data["default_require"])

    rules = data.get("rules", [])
    if not isinstance(rules, list):
        raise PolicyConfigError("policy.yml 'rules' must be a list")
    for i, entry in enumerate(rules):
        if not isinstance(entry, dict):
            raise PolicyConfigError(f"rules[{i}] must be a mapping")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise PolicyConfigError(f"rules[{i}] requires a non-empty 'name'")
        patterns = entry.get("paths")
        if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) and p for p in patterns):
            raise PolicyConfigError(f"rules[{i}] ('{name}') requires a non-empty 'paths' list of strings")
        require = _parse_require(f"rules[{i}].require", entry.get("require", []))
        unknown = set(entry) - {"name", "paths", "require"}
        if unknown:
            raise PolicyConfigError(f"rules[{i}] ('{name}') has unknown keys: {sorted(unknown)}")
        policy.rules.append(PolicyRule(name=name, patterns=list(patterns), require=require))

    return policy


def _require_bool(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise PolicyConfigError(f"settings.{key} must be a boolean")
    return value


def _parse_require(where: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise PolicyConfigError(f"{where} must be a list of evidence methods")
    for method in value:
        if method == "self_claimed":
            raise PolicyConfigError(f"{where}: self_claimed can never satisfy policy and may not be required")
        if method not in _REQUIRABLE:
            raise PolicyConfigError(f"{where}: unknown evidence method {method!r} (allowed: {list(_REQUIRABLE)})")
    return list(value)


def evaluate(policy: Policy, changed_paths: list[str], verified_methods: set[str]) -> PolicyDecision:
    """Decide acceptance for a set of changed paths given the evidence methods
    the gate actually verified. `verified_methods` must never contain
    self_claimed; guard anyway."""
    verified = set(verified_methods) - {"self_claimed"}
    findings: list[PathFinding] = []
    for path in changed_paths:
        rule_name = "(default)"
        require = policy.default_require
        for rule in policy.rules:
            if rule.matches(path):
                rule_name = rule.name
                require = rule.require
                break
        satisfied = not require or bool(set(require) & verified)
        findings.append(PathFinding(path=path, rule=rule_name, require=list(require), satisfied=satisfied))
    return PolicyDecision(passed=all(f.satisfied for f in findings), findings=findings)
