#!/usr/bin/env python3
"""Apply and check the auth-failure alerting in scripts/monitoring/ (#464, security gap G6).

The log-based metrics and alert policies are kept as files, next to the AR cleanup
policy, and this is the only way they reach a project:

    auth_alerting.py check --env test                       # read-only drift report
    auth_alerting.py apply --env test --email you@example.com [--email other@example.com]
    auth_alerting.py apply --env prod                       # prompts; reuses the channels

`apply` is idempotent: it creates a metric or policy that is missing and updates one that
exists (a policy is found by its displayName). Change the alerting by editing the files
and re-running this, never with a one-off gcloud command, or the next apply reverts it.

An email channel holds one address, so there is one channel per address, all with
displayName CHANNEL_NAME. `--email` (repeatable) creates a channel for each address that
doesn't have one yet; an existing channel is reused, and none is ever deleted (remove one in
the console). The addresses are never committed. Every policy is sent with all of the
channels merged in: a policy file carries no notificationChannels, and `gcloud monitoring
policies update --policy-from-file` replaces the whole policy, so updating from the bare
file would silently drop them.

An operator runs this, not CI: the deployer holds run.developer, which can't write
logging metrics or monitoring policies, and widening its IAM was declined (2026-10-05,
docs/architecture/cloudrun-services.md). Needs roles/logging.configWriter,
roles/monitoring.alertPolicyEditor and roles/monitoring.notificationChannelEditor.
Notification channels are still beta-only in gcloud (SDK 588); policies are GA.

Design, thresholds and their baseline: docs/proposals/auth-failure-alerting-plan.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MONITORING = ROOT / "scripts" / "monitoring"
INVENTORY = ROOT / "deploy" / "cloudrun-services.toml"
CHANNEL_NAME = "QuantCore security alerts (email)"
NOT_FOUND = ("NOT_FOUND", "not found", "does not exist")

# What `check` compares. Anything else the API adds (name, creationRecord, mutationRecord...)
# is the API's, not drift. `enabled` is compared: a disabled policy can't alert, and every
# policy file sets it explicitly so `apply` re-enables one.
METRIC_FIELDS = ("filter", "labelExtractors")
POLICY_FIELDS = ("enabled", "combiner", "severity", "conditions", "alertStrategy",
                 "documentation")


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- files

def metrics(directory: Path = MONITORING) -> dict[str, dict]:
    """``{metric name: config}``; a metric is named after its file's stem."""
    return {p.name.removesuffix(".metric.yaml"): yaml.safe_load(p.read_text())
            for p in sorted(directory.glob("*.metric.yaml"))}


def policies(directory: Path = MONITORING) -> list[dict]:
    out = [yaml.safe_load(p.read_text()) for p in sorted(directory.glob("*.policy.yaml"))]
    names = [p["displayName"] for p in out]
    if len(set(names)) != len(names):
        raise ConfigError(f"duplicate policy displayName in {directory}: {names}")
    return out


def project(env: str) -> str:
    envs = tomllib.loads(INVENTORY.read_text()).get("environments", {})
    if env not in envs:
        raise ConfigError(f"unknown environment {env!r} (have: {', '.join(envs)})")
    return envs[env]["project"]


# ---------------------------------------------------------------- comparison

def _norm(value):
    """Make a file value and an API value comparable.

    The API returns durations and numbers as it likes ("300s", 10 vs 10.0) and folds
    whitespace in filters, so compare whitespace-collapsed strings and floats.
    """
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def _shape(want, live):
    """``live`` cut down to the keys ``want`` sets.

    The API adds its own fields (each condition's ``name``, ``mutationRecord``...), which
    are not drift; a key the file sets and the live object lacks still shows as ``None``.
    """
    if isinstance(want, dict) and isinstance(live, dict):
        return {k: _shape(v, live.get(k)) for k, v in want.items()}
    if isinstance(want, list) and isinstance(live, list) and len(want) == len(live):
        return [_shape(w, v) for w, v in zip(want, live)]
    if live is None and _is_zero(want):
        # proto3 JSON omits a number at its default: the API returns no thresholdValue
        # for `thresholdValue: 0` (seen on the first apply to test, 2026-10-28).
        return want
    return live


def _is_zero(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def diff(want: dict, live: dict, fields: tuple[str, ...]) -> list[str]:
    """Fields where the file and the live object differ; fields the file omits are skipped."""
    out = []
    for k in fields:
        if k not in want:
            continue
        got = _shape(want[k], live.get(k))
        if _norm(want[k]) != _norm(got):
            out.append(f"{k}: live {json.dumps(got)}, file {json.dumps(want[k])}")
    return out


def with_channels(policy: dict, channels: list[str]) -> dict:
    """The policy as sent: the file plus the notification channels (see module doc)."""
    return {**policy, "notificationChannels": sorted(channels)}


# ---------------------------------------------------------------- gcloud

def gcloud(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = ["gcloud", *args]
    if not capture:
        print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, capture_output=capture, text=True)


def _json(r: subprocess.CompletedProcess, what: str):
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise RuntimeError(f"{what} failed (exit {r.returncode})")
    return json.loads(r.stdout or "null")


def live_metric(proj: str, name: str) -> dict | None:
    r = gcloud("logging", "metrics", "describe", name, "--project", proj, "--format=json")
    if r.returncode != 0 and any(s in r.stderr for s in NOT_FOUND):
        return None
    return _json(r, f"describing metric {name}")


def live_policies(proj: str) -> dict[str, dict]:
    r = gcloud("monitoring", "policies", "list", "--project", proj, "--format=json")
    found = _json(r, "listing alert policies") or []
    return {p["displayName"]: p for p in found}


def find_channels(proj: str) -> dict[str, str]:
    """``{email address: channel name}`` for every CHANNEL_NAME channel."""
    r = gcloud("beta", "monitoring", "channels", "list", "--project", proj,
               f"--filter=displayName=\"{CHANNEL_NAME}\"", "--format=json")
    found = _json(r, "listing notification channels") or []
    return {c.get("labels", {}).get("email_address", ""): c["name"] for c in found}


def create_channel(proj: str, email: str) -> str:
    r = gcloud("beta", "monitoring", "channels", "create", "--project", proj,
               f"--display-name={CHANNEL_NAME}", "--type=email",
               f"--channel-labels=email_address={email}", "--format=json")
    return _json(r, "creating the email channel")["name"]


def _run(*args: str) -> None:
    r = gcloud(*args, capture=False)
    if r.returncode != 0:
        raise RuntimeError(f"gcloud {' '.join(args[:3])} failed (exit {r.returncode})")


def _with_file(obj: dict, fn) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(obj, f)
    try:
        fn(f.name)
    finally:
        Path(f.name).unlink()


# ---------------------------------------------------------------- commands

def _status(lines: list[str] | None) -> str:
    """``None`` means the object doesn't exist; ``[]`` that it matches the file."""
    return "MISSING" if lines is None else ("DRIFT" if lines else "ok")


def _report(label: str, lines: list[str] | None) -> int:
    """Print one object's status; 1 if it differs from the file."""
    print(f"{label}: {_status(lines)}")
    for line in lines or []:
        print(f"  {line}")
    return int(lines != [])


def _policy_drift(want: dict, got: dict | None, channels: list[str]) -> list[str] | None:
    if got is None:
        return None
    lines = diff(want, got, POLICY_FIELDS)
    missing = set(channels) - set(got.get("notificationChannels", []))
    if missing:
        lines.append(f"notificationChannels: {len(missing)} {CHANNEL_NAME!r} not attached")
    return lines


def cmd_check(a) -> int:
    proj = project(a.env)
    drift = 0
    for name, want in metrics().items():
        live = live_metric(proj, name)
        drift += _report(f"metric {name}",
                         None if live is None else diff(want, live, METRIC_FIELDS))
    channels = list(find_channels(proj).values())
    drift += _report(f"channel {CHANNEL_NAME!r} x{len(channels)}", [] if channels else None)
    live = live_policies(proj)
    for want in policies():
        drift += _report(f"policy {want['displayName']!r}",
                         _policy_drift(want, live.get(want["displayName"]), channels))
    print(f"{a.env} ({proj}): {drift} object(s) differ from {MONITORING.relative_to(ROOT)}")
    return 1 if drift else 0


def _apply_metrics(proj: str) -> bool:
    """Create or update every metric; True if any was created this run."""
    created = False
    for name, want in metrics().items():
        verb = "update" if live_metric(proj, name) else "create"
        created |= verb == "create"
        _with_file(want, lambda path: _run(
            "logging", "metrics", verb, name, "--project", proj, f"--config-from-file={path}"))
    return created


def _resolve_channels(proj: str, emails: list[str]) -> list[str]:
    """Every CHANNEL_NAME channel, after creating one for each new address in ``emails``."""
    found = find_channels(proj)
    for email in dict.fromkeys(emails):
        if email in found:
            print(f"channel for {email} exists; reused")
            continue
        found[email] = create_channel(proj, email)
        print(f"created channel {CHANNEL_NAME!r} for {email}")
    if not found:
        raise ConfigError(f"no {CHANNEL_NAME!r} channel in {proj} yet: pass --email once")
    return list(found.values())


def _apply_policy(proj: str, want: dict, got: dict | None, channels: list[str]) -> None:
    args = ("create",) if got is None else ("update", got["name"])
    _with_file(with_channels(want, channels), lambda path: _run(
        "monitoring", "policies", *args, "--project", proj, f"--policy-from-file={path}"))


def cmd_apply(a) -> int:
    proj = project(a.env)
    print(f"Target: {proj}")
    if a.env == "prod" and not a.yes:
        if input("Apply the auth alerting to PROD? [y/N] ").strip() != "y":
            return 1
    created_metric = _apply_metrics(proj)
    channels = _resolve_channels(proj, a.email)
    live = live_policies(proj)
    try:
        for want in policies():
            _apply_policy(proj, want, live.get(want["displayName"]), channels)
    except RuntimeError:
        if created_metric:
            # A just-created log metric's descriptor can lag; everything here is
            # idempotent, so re-running is the fix.
            print("::warning::a metric was created this run; if the policy refers to "
                  "it, wait a minute and re-run apply", file=sys.stderr)
        raise
    return cmd_check(a)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="read-only: diff scripts/monitoring/ against a project")
    c.add_argument("--env", required=True, choices=("test", "prod"))
    ap = sub.add_parser("apply", help="create or update the metrics, channels and policies")
    ap.add_argument("--env", required=True, choices=("test", "prod"))
    ap.add_argument("--email", action="append", default=[],
                    help="an address to alert (repeatable); needed only for a new address")
    ap.add_argument("--yes", action="store_true", help="skip the prod confirmation")
    a = p.parse_args(argv)
    try:
        return {"check": cmd_check, "apply": cmd_apply}[a.cmd](a)
    except ConfigError as e:
        print(f"::error title=auth alerting::{e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"::error title=gcloud failed::{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
