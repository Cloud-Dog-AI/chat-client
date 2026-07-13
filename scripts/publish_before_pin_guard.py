#!/usr/bin/env python3
# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# Licensed under the Apache License, Version 2.0.
"""
W28C-1719 — Publish-BEFORE-Pin build guard (FAIL CLOSED).

Root-cause control for the shared-package publish-ordering defect class
(W28P-2406/2406A + W28R-3002): a source version was bumped and consumers were
re-pinned to it BEFORE the package was published to the internal mirror, so
consumer builds could not resolve the pin — and fail-open Dockerfiles (W28C-1718)
shipped BROKEN images that still reported "healthy".

This guard runs in a clean, credentialled-but-cache-cold environment and resolves
EVERY internal shared-package pin (namespace ``cloud-dog-*``) declared by a
consumer against the SINGLE approved internal index (pypi.cloud-dog.net) BEFORE
the build proceeds. Any internal pin that does not resolve makes the guard exit
non-zero (FAIL CLOSED). It never falls back to a second index and never queries a
Gitea/GitHub package boundary (COMMON-PACKAGE §0A.GH / PS-97 §3.3 single-index).

Usage:
    publish_before_pin_guard.py [CONSUMER_DIR]
Env:
    PIP_CONFIG_FILE   pip config with the single internal index-url (preferred).
    GUARD_INDEX_HOST  expected internal index host (default pypi.cloud-dog.net);
                      the guard refuses forbidden Gitea/GitHub index hosts.
Exit codes: 0 = all internal pins resolve · 2 = one or more UNRESOLVED (fail
closed) · 3 = misconfiguration (no index / forbidden boundary / no manifests).
"""
from __future__ import annotations
import os, re, subprocess, sys, tempfile, shutil, configparser

INTERNAL_PREFIX = "cloud-dog-"          # PEP-503 canonical namespace = internal
FORBIDDEN_HOSTS = ("gitea", "github")   # §0A.GH: never a build/package boundary

try:
    from packaging.utils import canonicalize_name
    from packaging.requirements import Requirement
except Exception:  # packaging is always present in the build venv; degrade safely
    def canonicalize_name(n): return re.sub(r"[-_.]+", "-", n).lower()
    Requirement = None


def _canon(name: str) -> str:
    return canonicalize_name(name)


def is_internal(name: str) -> bool:
    return _canon(name).startswith(INTERNAL_PREFIX)


_DEP_RE = re.compile(
    r'^\s*["\']?\s*(cloud[-_]dog[-_][A-Za-z0-9_.-]+)\s*(\[[^\]]*\])?\s*'
    r'([<>=!~][^"\';#]*)?')


def _clean_spec(spec: str) -> str:
    # drop trailing quotes/commas and any environment marker (`; python_version...`)
    spec = spec.split(";", 1)[0]
    return spec.strip().rstrip('",\'').strip()


def pins_from_requirements(text: str):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-"):
            continue
        m = _DEP_RE.match(s)
        if not m:
            continue
        name = m.group(1)
        if not is_internal(name):
            continue
        out.append((_canon(name), _clean_spec(m.group(3) or "")))
    return out


def pins_from_pyproject(text: str):
    out = []
    try:
        import tomllib as _toml  # Python >= 3.11
    except ModuleNotFoundError:
        try:
            import tomli as _toml  # backport for Python < 3.11
        except ModuleNotFoundError:
            # Do NOT line-scan pyproject.toml as if it were a requirements file:
            # that mis-reads [project.scripts] / name entries (e.g.
            # `cloud-dog-chat = "...:main"`, `name = "cloud-dog-chat-client"`) as
            # internal pins and fails the build with a false positive
            # (W28M-1624). Fail loud with an actionable message instead.
            raise RuntimeError(
                "pyproject.toml present but no TOML parser available: install "
                "'tomli' or run the guard under Python >= 3.11 (set GUARD_PYTHON "
                "to a 3.11+ interpreter)."
            )
    data = _toml.loads(text)
    deps = list(data.get("project", {}).get("dependencies", []) or [])
    for extras in (data.get("project", {}).get("optional-dependencies", {}) or {}).values():
        deps += list(extras or [])
    for dep in deps:
        m = _DEP_RE.match(str(dep).strip())
        if not m or not is_internal(m.group(1)):
            continue
        out.append((_canon(m.group(1)), _clean_spec(m.group(3) or "")))
    return out


def collect_internal_pins(consumer_dir: str):
    """Return {canonical_name: set(spec)} of internal pins found across manifests."""
    found = {}
    seen_files = []
    for fn in os.listdir(consumer_dir):
        path = os.path.join(consumer_dir, fn)
        if not os.path.isfile(path):
            continue
        if fn == "pyproject.toml":
            parser = pins_from_pyproject
        elif re.fullmatch(r"requirements.*\.txt", fn):
            parser = pins_from_requirements
        else:
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        pins = parser(text)
        if pins:
            seen_files.append(fn)
        for name, spec in pins:
            found.setdefault(name, set()).add(spec)
    return found, seen_files


def _all_index_urls_from_pip_config():
    """Every index-url / extra-index-url in the pip config (order preserved).
    Regex-based so an embedded '%' in a credential never trips configparser
    interpolation. Values are never logged by callers."""
    cfg = os.environ.get("PIP_CONFIG_FILE")
    urls = []
    if not cfg or not os.path.isfile(cfg):
        return urls
    try:
        with open(cfg, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\s*(?:extra-index-url|index-url)\s*=\s*(\S+)", line)
                if m:
                    urls.append(m.group(1))
    except Exception:
        pass
    return urls


def _internal_index_from_pip_config(expected_host):
    """Return (full_url_with_creds, host) of the INTERNAL mirror the guard will
    resolve internal pins against. Rejects Gitea/GitHub hosts (§0A.GH).
    Returns (None, reason) on misconfiguration."""
    from urllib.parse import urlsplit
    urls = _all_index_urls_from_pip_config()
    if not urls:
        return None, "no index-url/extra-index-url in PIP_CONFIG_FILE"
    for u in urls:
        host = (urlsplit(u).hostname or "").lower()
        if any(bad in host for bad in FORBIDDEN_HOSTS):
            return None, f"forbidden index host '{host}' (Gitea/GitHub; §0A.GH)"
    # prefer an internal cloud-dog host; else the exact expected host; else any
    for u in urls:
        host = (urlsplit(u).hostname or "")
        if host == expected_host or "cloud-dog" in host.lower():
            return u, host
    u = urls[0]
    return u, (urlsplit(u).hostname or "")


def resolve_one(python, name, spec, cache_dir, pipconf):
    """Cache-cold, no-deps resolution of a single internal pin against the
    guard's single-internal-index config. Returns (resolved, err_redacted)."""
    req = f"{name}{spec}" if spec else name
    # Sanitize ambient PIP_* so ONLY the guard's single-internal-index config is
    # authoritative (env PIP_INDEX_URL/PIP_EXTRA_INDEX_URL would otherwise
    # override the config file and silently change the index — false result).
    env = {k: v for k, v in os.environ.items() if not k.startswith("PIP_")}
    env["PIP_CONFIG_FILE"] = pipconf
    with tempfile.TemporaryDirectory() as dest:
        proc = subprocess.run(
            [python, "-m", "pip", "download", "--no-deps", "--no-cache-dir",
             "--cache-dir", cache_dir, "--dest", dest, "--no-input", req],
            capture_output=True, text=True, env=env)
    ok = proc.returncode == 0
    err = ""
    if not ok:
        for line in (proc.stdout + proc.stderr).splitlines():
            if "No matching distribution" in line or "Could not find" in line \
               or "ERROR" in line:
                err = line
                break
        err = re.sub(r"//[^@/]*@", "//<redacted>@", err or "unresolved")
    return ok, err


def main(argv):
    consumer_dir = argv[1] if len(argv) > 1 else "."
    python = os.environ.get("GUARD_PYTHON", sys.executable)
    expected_host = os.environ.get("GUARD_INDEX_HOST", "pypi.cloud-dog.net")

    internal_url, host_or_reason = _internal_index_from_pip_config(expected_host)
    if internal_url is None:
        print(f"PUBLISH_BEFORE_PIN_GUARD: MISCONFIG — {host_or_reason}.",
              file=sys.stderr)
        return 3
    host = host_or_reason
    if host != expected_host:
        # surfaced, not fatal: internal pins must resolve from the internal mirror
        print(f"PUBLISH_BEFORE_PIN_GUARD: note — internal index host '{host}' "
              f"(expected '{expected_host}').")

    pins, files = collect_internal_pins(consumer_dir)
    if not files:
        print(f"PUBLISH_BEFORE_PIN_GUARD: MISCONFIG — no requirements*.txt / "
              f"pyproject.toml in {consumer_dir}.", file=sys.stderr)
        return 3
    if not pins:
        print(f"PUBLISH_BEFORE_PIN_GUARD: PASS — no internal ({INTERNAL_PREFIX}*) "
              f"pins declared in {os.path.basename(os.path.abspath(consumer_dir))} "
              f"({', '.join(files)}); nothing to gate.")
        return 0

    cache_dir = tempfile.mkdtemp(prefix="pbp-cachecold-")
    # guard's OWN single-index config = internal mirror ONLY, so an internal name
    # can never be satisfied by a public typosquat (false PASS). Never logged.
    pipconf = os.path.join(cache_dir, "pip.conf")
    with open(pipconf, "w") as f:
        f.write(f"[global]\nindex-url = {internal_url}\ntrusted-host = {host}\n")
    os.chmod(pipconf, 0o600)
    unresolved, resolved = [], []
    try:
        for name in sorted(pins):
            for spec in sorted(pins[name]):
                ok, err = resolve_one(python, name, spec, cache_dir, pipconf)
                label = f"{name}{spec or ' (unpinned)'}"
                if ok:
                    resolved.append(label)
                else:
                    unresolved.append((label, err))
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)

    who = os.path.basename(os.path.abspath(consumer_dir))
    print(f"PUBLISH_BEFORE_PIN_GUARD: consumer={who} index={host} "
          f"internal_pins={len(resolved)+len(unresolved)} "
          f"resolved={len(resolved)} unresolved={len(unresolved)} "
          f"(files: {', '.join(files)})")
    for lbl in resolved:
        print(f"  RESOLVED   {lbl}")
    for lbl, err in unresolved:
        print(f"  UNRESOLVED {lbl}    <- {err}")

    if unresolved:
        print("PUBLISH_BEFORE_PIN_GUARD: FAIL failures="
              f"{len(unresolved)} — internal pin(s) not published to the mirror; "
              "publish BEFORE pinning. Build fails closed.", file=sys.stderr)
        return 2
    print("PUBLISH_BEFORE_PIN_GUARD: PASS failures=0 — every internal pin resolves "
          "from the internal mirror; publish-before-pin ordering holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
