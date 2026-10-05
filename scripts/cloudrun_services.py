#!/usr/bin/env python3
"""Deploy and check the Cloud Run services listed in deploy/cloudrun-services.toml (#161).

Both roll-out workflows call this instead of carrying per-service gcloud commands, so the
inventory file is the one place a service's config lives:

    cloudrun_services.py names  --env test --phase 2
    cloudrun_services.py deploy quantcore-portfolio --env test --registry REG --tag abc1234
    cloudrun_services.py deploy quantcore-api --env prod --registry REG --by-digest
    cloudrun_services.py check  --env prod          # read-only drift report

`deploy` on an EXISTING service rolls the new image and passes the manifest's scalar
shape (port, cpu, memory, timeout, concurrency, max instances, ingress, service account,
cpu boost) on every roll-out, as the old sizing env block did. Env vars, secrets and
Cloud SQL instances are passed only where the live service differs from the manifest,
and only with --update-*/--add-*, so a key the manifest doesn't mention is kept. IAM is
never touched on an existing service: a public service without its allUsers binding
fails the deploy instead (a deployer holding only run.developer can't grant it).

`deploy` on a MISSING service creates it with its full config when its first_create is
"auto" (only public services may be), then binds allUsers as run.invoker; for "manual"
it fails, naming the runbook. Never --set-*: on an existing service it replaces the
whole set (it took prod down on 2026-07-18).

Never prints a secret's value: the manifest holds only Secret Manager references.
Onboarding and the IAM model: docs/architecture/cloudrun-services.md.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "deploy" / "cloudrun-services.toml"

REQUIRED = ("name", "image", "phase", "first_create", "auth", "port", "cpu", "memory",
            "cpu_boost", "timeout", "concurrency", "max_instances", "ingress",
            "service_account")
FIRST_CREATE = ("auto", "manual")
AUTH = ("public", "iap", "private")
NOT_FOUND = "Cannot find service"


class ManifestError(Exception):
    pass


# ---------------------------------------------------------------- manifest

def _resolve(value, env_name, ctx, where):
    """A per-environment table picks this env's value; a string has its templates filled."""
    if isinstance(value, dict):
        if env_name not in value:
            raise ManifestError(f"{where}: no value for environment {env_name!r}")
        value = value[env_name]
    if isinstance(value, str):
        try:
            return value.format_map(ctx)
        except KeyError as e:
            raise ManifestError(f"{where}: unknown template {{{e.args[0]}}}") from None
    return value


def load(env_name: str, path: Path | None = None) -> list[dict]:
    """Every service deployed to `env_name`, fully resolved and validated."""
    doc = tomllib.loads((path or MANIFEST).read_text())
    envs = doc.get("environments", {})
    if env_name not in envs:
        raise ManifestError(f"unknown environment {env_name!r} (have: {', '.join(envs)})")
    ctx = dict(envs[env_name])
    ctx["runtime_sa"] = ctx["runtime_sa"].format_map(ctx)
    ctx["api_url"] = ctx["api_url"].format_map(ctx)

    out, seen = [], set()
    for raw in doc.get("services", []):
        name = raw.get("name", "?")
        missing = [k for k in REQUIRED if k not in raw]
        if missing:
            raise ManifestError(f"{name}: missing {', '.join(missing)}")
        if name in seen:
            raise ManifestError(f"{name}: listed twice")
        seen.add(name)
        if env_name not in raw.get("environments", list(envs)):
            continue
        s = {k: _resolve(v, env_name, ctx, f"{name}.{k}")
             for k, v in raw.items() if k not in ("env", "secrets", "cloudsql")}
        s["env"] = {k: _resolve(v, env_name, ctx, f"{name}.env.{k}")
                    for k, v in raw.get("env", {}).items()}
        s["secrets"] = {k: _resolve(v, env_name, ctx, f"{name}.secrets.{k}")
                        for k, v in raw.get("secrets", {}).items()}
        s["cloudsql"] = [_resolve(v, env_name, ctx, f"{name}.cloudsql")
                         for v in raw.get("cloudsql", [])]
        s["project"], s["region"] = ctx["project"], ctx["region"]
        _validate(s)
        out.append(s)
    return out


def _validate(s: dict) -> None:
    name = s["name"]
    if s["phase"] not in (1, 2):
        raise ManifestError(f"{name}: phase must be 1 or 2")
    if s["first_create"] not in FIRST_CREATE:
        raise ManifestError(f"{name}: first_create must be one of {FIRST_CREATE}")
    if s["auth"] not in AUTH:
        raise ManifestError(f"{name}: auth must be one of {AUTH}")
    if s["first_create"] == "manual" and not s.get("runbook"):
        raise ManifestError(f"{name}: a manual first_create needs a runbook")
    # CI can only bind allUsers; an IAP or private service's access is a manual grant.
    if s["first_create"] == "auto" and s["auth"] != "public":
        raise ManifestError(f"{name}: only a public service can be auto-created")
    for key in (*s["env"], *s["secrets"]):
        if key == "PORT":
            raise ManifestError(f"{name}: PORT is reserved; Cloud Run sets it from `port`")
    if set(s["env"]) & set(s["secrets"]):
        raise ManifestError(f"{name}: a key is both an env var and a secret")
    for key, value in {**s["env"], **s["secrets"]}.items():
        if "," in value:  # gcloud's list separator
            raise ManifestError(f"{name}: {key} contains a comma")


# ---------------------------------------------------------------- gcloud

def gcloud(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["gcloud", *args]
    if not capture:
        print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, capture_output=capture, text=True)


def describe(s: dict) -> dict | None:
    """The live service, or None when it doesn't exist. Any other failure raises."""
    r = gcloud("run", "services", "describe", s["name"], "--project", s["project"],
               "--region", s["region"], "--format=json", capture=True)
    if r.returncode == 0:
        return json.loads(r.stdout)
    if NOT_FOUND in r.stderr:
        return None
    sys.stderr.write(r.stderr)
    raise RuntimeError(f"describing {s['name']} failed (exit {r.returncode})")


def has_public_invoker(s: dict) -> bool:
    r = gcloud("run", "services", "get-iam-policy", s["name"], "--project", s["project"],
               "--region", s["region"], "--format=json", capture=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise RuntimeError(f"reading {s['name']}'s IAM policy failed (exit {r.returncode})")
    policy = json.loads(r.stdout or "{}")
    return any(b.get("role") == "roles/run.invoker" and "allUsers" in b.get("members", [])
               for b in policy.get("bindings", []))


def live_config(svc: dict) -> dict:
    """The fields the manifest manages, read out of `gcloud run services describe`."""
    tmpl = svc["spec"]["template"]
    ann = tmpl.get("metadata", {}).get("annotations", {})
    spec = tmpl["spec"]
    c = spec["containers"][0]
    env, secrets = {}, {}
    for e in c.get("env", []):
        ref = e.get("valueFrom", {}).get("secretKeyRef")
        if ref:
            secrets[e["name"]] = f"{ref['name']}:{ref.get('key', 'latest')}"
        else:
            env[e["name"]] = e.get("value", "")
    sql = ann.get("run.googleapis.com/cloudsql-instances", "")
    return {
        "port": int(c.get("ports", [{}])[0].get("containerPort", 8080)),
        "cpu": str(c.get("resources", {}).get("limits", {}).get("cpu", "")),
        "memory": c.get("resources", {}).get("limits", {}).get("memory", ""),
        "cpu_boost": ann.get("run.googleapis.com/startup-cpu-boost") == "true",
        "timeout": int(spec.get("timeoutSeconds", 0)),
        "concurrency": int(spec.get("containerConcurrency", 0)),
        "max_instances": int(ann.get("autoscaling.knative.dev/maxScale", 0)),
        "ingress": svc["metadata"].get("annotations", {}).get("run.googleapis.com/ingress"),
        "service_account": spec.get("serviceAccountName", ""),
        "env": env,
        "secrets": secrets,
        "cloudsql": [x for x in sql.split(",") if x],
    }


SCALARS = ("port", "cpu", "memory", "cpu_boost", "timeout", "concurrency", "max_instances",
           "ingress", "service_account")


def diff(s: dict, live: dict) -> list[str]:
    """Human-readable differences, manifest vs live. Extra live keys are kept, not drift."""
    out = [f"{k}: live {live[k]!r}, manifest {s[k]!r}" for k in SCALARS if live[k] != s[k]]
    for kind in ("env", "secrets"):
        for k, v in s[kind].items():
            if live[kind].get(k) != v:
                shown = live[kind].get(k, "<unset>")
                out.append(f"{kind}.{k}: live {shown!r}, manifest {v!r}")
    for inst in s["cloudsql"]:
        if inst not in live["cloudsql"]:
            out.append(f"cloudsql: {inst} not attached")
    return out


def deploy_args(s: dict, image: str, live: dict | None) -> list[str]:
    args = ["run", "deploy", s["name"], "--project", s["project"], "--region", s["region"],
            "--image", image,
            "--port", str(s["port"]), "--cpu", s["cpu"], "--memory", s["memory"],
            "--timeout", str(s["timeout"]), "--concurrency", str(s["concurrency"]),
            "--max-instances", str(s["max_instances"]), "--ingress", s["ingress"],
            "--service-account", s["service_account"],
            "--cpu-boost" if s["cpu_boost"] else "--no-cpu-boost"]
    have = live or {"env": {}, "secrets": {}, "cloudsql": []}
    env = {k: v for k, v in s["env"].items() if have["env"].get(k) != v}
    sec = {k: v for k, v in s["secrets"].items() if have["secrets"].get(k) != v}
    sql = [x for x in s["cloudsql"] if x not in have["cloudsql"]]
    if env:
        args += ["--update-env-vars", ",".join(f"{k}={v}" for k, v in env.items())]
    if sec:
        args += ["--update-secrets", ",".join(f"{k}={v}" for k, v in sec.items())]
    if sql:
        args += ["--add-cloudsql-instances", ",".join(sql)]
    # No --[no-]allow-unauthenticated: either one makes gcloud set IAM, which a deployer
    # with run.developer can't. With --quiet and neither flag, it makes no IAM call.
    return args + ["--quiet"]


def grant_command(s: dict) -> str:
    return (f"gcloud run services add-iam-policy-binding {s['name']} --project {s['project']} "
            f"--region {s['region']} --member=allUsers --role=roles/run.invoker")


# ---------------------------------------------------------------- subcommands

def find(env_name: str, name: str) -> dict:
    for s in load(env_name):
        if s["name"] == name:
            return s
    raise ManifestError(f"{name} is not in {MANIFEST.name} for {env_name}")


def cmd_names(a) -> int:
    for s in load(a.env):
        if a.phase is None or s["phase"] == a.phase:
            print(s["name"])
    return 0


def cmd_deploy(a) -> int:
    s = find(a.env, a.name)
    if a.by_digest:
        var = s["image"].upper().replace("-", "_") + "_DIGEST"
        digest = os.environ.get(var, "")
        if not digest:
            if s.get("skip_without_digest"):
                print(f"{s['image']} was not promoted for this tag (no {var}); skipping {s['name']}.")
                return 0
            print(f"::error title={s['name']} has no image::{var} is unset; "
                  f"{s['image']} was not promoted.")
            return 1
        image = f"{a.registry}/{s['image']}@{digest}"
    else:
        image = f"{a.registry}/{s['image']}:{a.tag}"

    live = describe(s)
    if live is None:
        if s["first_create"] != "auto":
            print(f"::error title={s['name']} missing::{s['name']} does not exist in "
                  f"{s['project']} and its first deploy is manual: {s['runbook']}. "
                  f"Nothing was deployed for it.")
            return 1
        print(f"{s['name']} does not exist in {s['project']}; creating it from the inventory.")
        if gcloud(*deploy_args(s, image, None)).returncode != 0:
            return 1
        if gcloud("run", "services", "add-iam-policy-binding", s["name"],
                  "--project", s["project"], "--region", s["region"],
                  "--member=allUsers", "--role=roles/run.invoker").returncode != 0:
            print(f"::error title={s['name']} not public::{s['name']} was created but the "
                  f"deployer could not make it public. An owner runs: {grant_command(s)}")
            return 1
        return 0

    cfg = live_config(live)
    for line in diff(s, cfg):
        print(f"{s['name']} differs from the inventory, applying: {line}")
    if gcloud(*deploy_args(s, image, cfg)).returncode != 0:
        return 1
    if s["auth"] == "public" and not has_public_invoker(s):
        print(f"::error title={s['name']} not public::{s['name']} has no allUsers run.invoker "
              f"binding, so every client gets 403. An owner runs: {grant_command(s)}")
        return 1
    return 0


def cmd_check(a) -> int:
    drift = 0
    for s in load(a.env):
        live = describe(s)
        if live is None:
            if s["first_create"] == "auto":
                print(f"{s['name']}: MISSING (first_create=auto; the next roll-out creates it)")
            else:
                print(f"{s['name']}: MISSING (manual first deploy: {s['runbook']})")
                drift += 1
            continue
        lines = diff(s, live_config(live))
        public = has_public_invoker(s)
        if public != (s["auth"] == "public"):
            lines.append(f"auth: allUsers invoker {'present' if public else 'absent'}, "
                         f"manifest auth={s['auth']}")
        drift += bool(lines)
        print(f"{s['name']}: {'DRIFT' if lines else 'ok'}")
        for line in lines:
            print(f"  {line}")
    print(f"{a.env}: {drift} service(s) differ from {MANIFEST.name}")
    return 1 if drift else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("names", help="service names, for the workflow's phases")
    n.add_argument("--env", required=True)
    n.add_argument("--phase", type=int)
    d = sub.add_parser("deploy", help="roll out (or create) one service")
    d.add_argument("name")
    d.add_argument("--env", required=True)
    d.add_argument("--registry", required=True, help="REGION-docker.pkg.dev/PROJECT/REPO")
    how = d.add_mutually_exclusive_group(required=True)
    how.add_argument("--tag")
    how.add_argument("--by-digest", action="store_true",
                     help="image@$<IMAGE>_DIGEST, as prod-rollout's promotion exports it")
    c = sub.add_parser("check", help="read-only: diff the inventory against live services")
    c.add_argument("--env", required=True)
    a = p.parse_args(argv)
    try:
        return {"names": cmd_names, "deploy": cmd_deploy, "check": cmd_check}[a.cmd](a)
    except ManifestError as e:
        # stderr: the workflow captures `names` stdout into a variable, which would hide it.
        print(f"::error title=service inventory::{e}", file=sys.stderr)
        return 2
    except RuntimeError as e:  # gcloud failed in a way that isn't "not found"
        print(f"::error title=gcloud failed::{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
