"""Config-file assertions for the release-build pipeline.

These tests guard contracts that ``test_build_compile_pipeline.py``
can't cover from Python — the configuration of sibling tools (Vite,
TypeScript, esbuild, GitHub Actions, ``scripts/install.js``).

Reused infrastructure (no path strings duplicated across tests):

  ``conftest.workspace_members``     → name → Path map from root ``workspaces``
  ``conftest.root``                  → project_root() for top-level files
  ``cli.commands.build.COMPILEALL_SOURCE_DIRS``
                                     → SSOT for the bytecode-compile path
                                       list shared with ``scripts/install.js``
  ``yaml`` (PyYAML, server dep)      → structured workflow parsing
  ``json`` (stdlib)                  → plain JSON files

tsconfig.json is JSONC (allows ``/* */`` comments and trailing commas).
The project has no JSONC parser; we use targeted ``re.search`` against
the raw text rather than reach for a fresh dependency. The TypeScript
team itself uses regex-on-source for tsconfig conformance tests.

Refer to ``docs-internal/release_build_pipeline.md`` for the rationale
behind each compile-pipeline knob.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from cli.commands import build


# ---------------------------------------------------------------------------
# Workspace-keyed fixtures (no path strings)
# ---------------------------------------------------------------------------

# Workspace package names. These are the stable IDs declared in each
# `package.json` — paths are resolved via the `workspace_members`
# fixture so tests don't care where the workspace lives on disk.
SIDECAR_PKG_NAME = "opencompany-nodejs-executor"
CLIENT_PKG_NAME = "react-flow-client"


def _load_pkg_json(workspace_path: Path) -> dict:
    return json.loads((workspace_path / "package.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sidecar_dir(workspace_members: dict[str, Path]) -> Path:
    return workspace_members[SIDECAR_PKG_NAME]


@pytest.fixture(scope="module")
def client_dir(workspace_members: dict[str, Path]) -> Path:
    return workspace_members[CLIENT_PKG_NAME]


@pytest.fixture(scope="module")
def sidecar_pkg(sidecar_dir: Path) -> dict:
    return _load_pkg_json(sidecar_dir)


@pytest.fixture(scope="module")
def client_pkg(client_dir: Path) -> dict:
    return _load_pkg_json(client_dir)


@pytest.fixture(scope="module")
def predeploy_yml(root: Path) -> dict:
    """``.github/workflows/predeploy.yml`` parsed via PyYAML so tests
    walk ``jobs[*].steps[*]`` structurally rather than regex on raw YAML.
    """
    path = root / ".github" / "workflows" / "predeploy.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def install_js_src(root: Path) -> str:
    return (root / "scripts" / "install.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def root_pkg(root: Path) -> dict:
    return _load_pkg_json(root)


@pytest.fixture(scope="module")
def release_yml(root: Path) -> dict:
    path = root / ".github" / "workflows" / "release.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def preinstall_js_src(root: Path) -> str:
    return (root / "scripts" / "preinstall.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# JS executor sidecar (runs on bun) — package.json & .gitignore
# ---------------------------------------------------------------------------


def test_sidecar_start_runs_compiled_bundle_on_bun(sidecar_pkg: dict):
    """``start`` runs the ``bun build`` output on bun, not interpreted
    TypeScript. The bundle is what ``nodes/code/_runtime.py`` spawns
    (``[bun, dist/index.js]``); the runtime is bun, never node.
    """
    start = sidecar_pkg["scripts"]["start"]
    assert start == "bun dist/index.js", f"sidecar start must be `bun dist/index.js`, got {start!r}"
    assert "node " not in start and "tsx" not in start


def test_sidecar_dev_uses_bun_watch(sidecar_pkg: dict):
    """``dev`` runs the TS source directly under ``bun --watch`` — bun
    executes TypeScript natively, so no loader (tsx) is needed."""
    dev = sidecar_pkg["scripts"]["dev"]
    assert dev.startswith("bun --watch") and "src/index.ts" in dev
    assert "tsx" not in dev


@pytest.mark.parametrize(
    "flag",
    [
        "bun build",
        "src/index.ts",
        "--target=bun",
        "--outfile=dist/index.js",
    ],
)
def test_sidecar_build_script_carries_required_bun_build_flag(sidecar_pkg: dict, flag: str):
    """Each part of the sidecar build script is load-bearing.

    - ``bun build`` — bun's bundler; no esbuild dependency.
    - ``--target=bun`` — self-contained bundle with express INLINED, so
      the desktop stage copies one file and no ``node_modules`` (bun's
      isolated linker would otherwise ship a dangling express symlink).
    - ``--outfile=dist/index.js`` — ``start`` and ``main`` read this
      exact path; mismatch breaks the runtime.
    """
    cmd = sidecar_pkg["scripts"]["build"]
    assert flag in cmd, f"sidecar build script missing {flag!r}: {cmd}"
    assert "esbuild" not in cmd and "--packages=external" not in cmd


def test_sidecar_engines_declare_bun_not_node(sidecar_pkg: dict):
    """The sidecar's runtime is bun; ``engines`` must say so and must not
    reintroduce a Node floor."""
    engines = sidecar_pkg.get("engines", {})
    assert "bun" in engines, f"engines must declare bun, got {engines!r}"
    assert "node" not in engines


def test_sidecar_main_field_points_at_compiled_output(sidecar_pkg: dict):
    """``main`` is what ``import 'opencompany-nodejs-executor'`` resolves
    to — the compiled bundle, not the TS source.
    """
    assert sidecar_pkg.get("main") == "dist/index.js"


def test_sidecar_has_no_node_toolchain_dependencies(sidecar_pkg: dict):
    """bun bundles and runs the sidecar; esbuild and tsx are gone."""
    deps = {**sidecar_pkg.get("dependencies", {}), **sidecar_pkg.get("devDependencies", {})}
    assert "esbuild" not in deps
    assert "tsx" not in deps


def test_sidecar_dist_is_gitignored(sidecar_dir: Path):
    """``dist/`` is build output and must not be tracked. Tracking it
    triggers noisy diffs every time the bundle is regenerated.
    """
    gitignore = (sidecar_dir / ".gitignore").read_text(encoding="utf-8")
    lines = {line.strip() for line in gitignore.splitlines()}
    assert {
        "dist/",
        "dist",
    } & lines, f"sidecar .gitignore must list dist/, got:\n{gitignore}"


# ---------------------------------------------------------------------------
# Client TypeScript / Vite
# ---------------------------------------------------------------------------


def test_client_typecheck_delegates_to_the_root_gate(
    client_pkg: dict, root_pkg: dict
):
    """The type-check gate runs TypeScript 7 (the native Go compiler),
    which lives at the ROOT rather than in the client.

    Placement is forced, not stylistic: ``client`` already depends on
    ``typescript@^5.9.3`` (typescript-eslint needs the JS API), and one
    manifest cannot declare the same package twice. Worse, ``typescript``
    7 and 5 both ship a ``tsc`` bin, so co-locating them would leave the
    package manager's bin-link conflict resolution deciding which
    compiler gates CI. (Under bun's isolated linker each package resolves
    its own ``.bin/tsc`` — root gets 7, client gets 5 — verified during
    the pnpm→bun migration.)

    The client script therefore delegates upward, which keeps the CI
    command (``bun run --filter react-flow-client typecheck``) and its
    guard below unchanged. Note bun 1.4 requires the ``--cwd=..`` form —
    the space-separated ``--cwd ..`` is rejected by ``bun run``.
    """
    assert client_pkg["scripts"].get("typecheck") == "bun --cwd=.. run typecheck", (
        "client typecheck must delegate to the root gate, got "
        f"{client_pkg['scripts'].get('typecheck')!r}"
    )
    assert (
        root_pkg["scripts"].get("typecheck") == "tsc --noEmit -p client/tsconfig.json"
    ), f"root typecheck must run TS7 against the client project, got {root_pkg['scripts'].get('typecheck')!r}"
    assert client_pkg["scripts"].get("typecheck:tsc") == "tsc --noEmit", (
        "typecheck:tsc must remain available as a TS 5.9 SECOND OPINION for "
        "triaging a red gate (is this my code, or a known typescript-go bug?). "
        "It is NOT an equivalent check and NOT a revert path: the two "
        "compilers check different programs under different default rules, "
        "and no CI lane proves this script still passes."
    )


def test_typescript_is_pinned_exactly_at_the_root(root_pkg: dict):
    """The compiler that gates CI must never move on an unrelated install.

    An exact pin makes a compiler change an explicit, reviewable commit.
    """
    version = root_pkg.get("devDependencies", {}).get("typescript")
    assert version is not None, "typescript must be in ROOT devDependencies"
    assert not version.startswith(
        ("^", "~", ">=", ">", "<")
    ), f"the gate compiler must be pinned exactly (no range prefix), got {version!r}"
    assert version.startswith("7."), (
        f"the gate must run TypeScript 7 (the native Go compiler), got {version!r}"
    )


def test_dead_native_preview_channel_does_not_creep_back(client_pkg: dict):
    """``@typescript/native-preview`` shipped the Go compiler before it
    landed in mainline ``typescript``. That channel stopped publishing at
    the 7.0 GA (last build 2026-07-07) and its bin is ``tsgo``, not
    ``tsc`` — so a reinstall of it would silently fork the gate.
    """
    assert "@typescript/native-preview" not in client_pkg.get("devDependencies", {}), (
        "@typescript/native-preview is a dead channel superseded by "
        "typescript@7 — the native compiler now ships in the mainline package"
    )


def test_client_keeps_typescript_5_for_typescript_eslint(client_pkg: dict):
    """Do NOT 'clean this up' by unifying on one TypeScript.

    ``typescript-eslint`` loads the TypeScript API at module scope and
    declares a peer range that excludes 6.x and 7.x. Under pnpm,
    ``strict-peer-dependencies=true`` made a bad bump a hard install
    failure; bun has no strict-peer mode and never errors on peer
    conflicts (oven-sh/bun#9135), so THIS TEST is now the only guard
    keeping the client on a TypeScript major that typescript-eslint's
    peer range accepts.
    """
    version = client_pkg.get("dependencies", {}).get("typescript")
    assert version is not None and version.startswith("^5."), (
        "client must keep typescript ^5.x for typescript-eslint's peer range; "
        f"got {version!r}"
    )


@pytest.mark.parametrize(
    "chunk_name",
    [
        "vendor-react",
        "vendor-flow",
        "vendor-radix",
        "vendor-icons",
        "vendor-query",
        "vendor-markdown",
    ],
)
def test_vite_config_declares_required_vendor_chunk(client_dir: Path, chunk_name: str):
    """Vite must split the heavy npm libs into named vendor chunks so
    the main bundle stays lean and dep churn doesn't bust the user's
    HTTP cache.
    """
    src = (client_dir / "vite.config.js").read_text(encoding="utf-8")
    assert "manualChunks" in src
    assert chunk_name in src, f"vite.config.js missing chunk {chunk_name!r}"


def test_vite_config_splits_reactflow(client_dir: Path):
    """reactflow specifically must be split out of the main bundle —
    it's the largest legitimate library and only canvas pages need it.
    """
    src = (client_dir / "vite.config.js").read_text(encoding="utf-8")
    assert "reactflow" in src


def test_vite_config_targets_es2022(client_dir: Path):
    """ES2022 unlocks native ``findLast``, optional-chaining
    assignment, class-fields without polyfills (Chrome 94+, FF 93+,
    Safari 15.4+ — within React 19 / Tailwind 4's baseline).
    """
    src = (client_dir / "vite.config.js").read_text(encoding="utf-8")
    assert re.search(
        r"target:\s*['\"]es2022['\"]", src
    ), "vite.config.js must declare `target: 'es2022'` in build options"


def test_vite_config_chunk_warning_below_one_megabyte(client_dir: Path):
    """The chunk-size warning must be tighter than the original 1500 KB
    so future regressions surface at build time.
    """
    src = (client_dir / "vite.config.js").read_text(encoding="utf-8")
    match = re.search(r"chunkSizeWarningLimit:\s*(\d+)", src)
    assert match is not None, "chunkSizeWarningLimit must be set in vite.config.js"
    assert int(match.group(1)) <= 1000, (
        f"chunkSizeWarningLimit={match.group(1)} is too lax — must be ≤1000 "
        "to catch chunk-size regressions"
    )


def test_tsconfig_drops_baseurl_and_sets_typeroots(client_dir: Path):
    """TS 7 / tsgo removed the deprecated ``baseUrl`` option.
    ``typeRoots`` is the explicit replacement for telling the compiler
    where to find ``@types/*`` packages — needed because the symlinked
    ``node_modules`` layout (pnpm before, bun's isolated linker now)
    can confuse auto-discovery.
    """
    src = (client_dir / "tsconfig.json").read_text(encoding="utf-8")
    assert not re.search(r'^\s*"baseUrl"', src, re.MULTILINE), (
        "TS 7 removed baseUrl — remove it; paths still resolve relative "
        "to tsconfig.json without it"
    )
    assert re.search(
        r'"typeRoots":\s*\[\s*"\./node_modules/@types"\s*\]',
        src,
    ), "typeRoots must point at ./node_modules/@types for tsgo + symlinked node_modules"
    # The path alias must survive the baseUrl removal.
    assert re.search(
        r'"@/\*":\s*\["\./src/\*"\]', src
    ), "the `@/*` -> `./src/*` alias must remain configured"


def test_vite_env_dts_references_google_maps(client_dir: Path):
    """``@types/google.maps`` exposes a global ``google.maps`` namespace
    (no module export). tsgo's auto-discovery doesn't pick it up
    reliably through symlinked node_modules (pnpm before, bun's isolated
    linker now), so the canonical Google-recommended
    pattern (a triple-slash reference) is applied once in the
    Vite-injected ambient declarations file.

    Ref: https://developers.google.com/maps/documentation/javascript/using-typescript
    """
    src = (client_dir / "src" / "vite-env.d.ts").read_text(encoding="utf-8")
    assert re.search(
        r"///\s*<reference\s+types=[\"']google\.maps[\"']\s*/>", src
    ), "vite-env.d.ts must triple-slash-reference google.maps"


def test_test_setup_intersection_observer_has_scroll_margin(client_dir: Path):
    """TS 7's lib.dom.d.ts (and Chromium 120+) requires
    ``scrollMargin: string`` on IntersectionObserver. The vitest stub
    must declare it or type-checks fail.
    """
    src = (client_dir / "src" / "test" / "setup.ts").read_text(encoding="utf-8")
    assert "IntersectionObserverStub" in src
    assert re.search(
        r"scrollMargin\s*=\s*['\"]['\"]", src
    ), "IntersectionObserverStub must set scrollMargin to satisfy TS 7"


# ---------------------------------------------------------------------------
# CI gate — predeploy.yml
# ---------------------------------------------------------------------------


def _step_by_name(job: dict, step_name: str) -> dict | None:
    for step in job.get("steps", []):
        if step.get("name") == step_name:
            return step
    return None


def test_predeploy_typecheck_step_routes_through_workspace_filter(predeploy_yml: dict):
    """The CI typecheck step must invoke the npm script (which delegates
    up to the root TypeScript 7 gate) rather than calling ``tsc``
    directly. Keeps the compiler choice in one place — the ROOT
    ``package.json`` — so it can change without editing the workflow.

    Bun's flag placement matters: ``--filter`` must come AFTER the
    ``run`` subcommand, and the script name goes last (no ``run`` between
    the filter pattern and the script, unlike pnpm).
    """
    job = predeploy_yml["jobs"]["build-and-lint"]
    step = _step_by_name(job, "TypeScript check")
    assert step is not None, "predeploy.yml must define a `TypeScript check` step"
    cmd = step.get("run", "")
    assert cmd.startswith("bun run --filter"), (
        f"predeploy.yml typecheck step must call `bun run --filter ... typecheck`, got {cmd!r}"
    )
    assert (
        CLIENT_PKG_NAME in cmd
    ), f"predeploy.yml typecheck step must filter to {CLIENT_PKG_NAME}, got {cmd!r}"
    assert cmd.rstrip().endswith("typecheck"), (
        f"predeploy.yml typecheck step must end with the script name, got {cmd!r}"
    )
    assert "tsc --noEmit" not in cmd, (
        "predeploy.yml still calls `tsc --noEmit` directly — switch to "
        "the typecheck script so the root TypeScript 7 gate is used"
    )


# ---------------------------------------------------------------------------
# Release publication — package.json, release.yml, preinstall.js
# ---------------------------------------------------------------------------


def _run_steps(job: dict) -> list[dict]:
    return [step for step in job.get("steps", []) if "run" in step]


def test_root_package_uses_public_zeenie_scope(root_pkg: dict):
    assert root_pkg["name"] == "@zeenie-ai/opencompany"
    assert root_pkg["publishConfig"] == {
        "access": "public",
        "registry": "https://registry.npmjs.org",
    }
    assert root_pkg["bin"] == {
        "company": "./bin/cli.js",
        "machina": "./bin/machina.js",
    }


def test_root_package_uses_canonical_github_urls(root_pkg: dict):
    canonical = "https://github.com/zeenie-ai/OpenCompany"
    assert root_pkg["homepage"] == f"{canonical}#readme"
    assert root_pkg["repository"] == {
        "type": "git",
        "url": f"git+{canonical}.git",
    }
    assert root_pkg["bugs"] == {"url": f"{canonical}/issues"}


def test_npm_release_authenticates_before_publish(
    release_yml: dict,
):
    """``bun pm whoami`` proves the token before ``bun publish`` runs, both
    reading the same ``~/.npmrc`` the auth step wrote from NPM_TOKEN."""
    steps = _run_steps(release_yml["jobs"]["publish-npm"])
    auth_index = next(i for i, step in enumerate(steps) if "_authToken" in step["run"])
    preflight_index = next(i for i, step in enumerate(steps) if "bun pm whoami" in step["run"])
    publish_index = next(i for i, step in enumerate(steps) if "bun publish" in step["run"])

    assert steps[auth_index]["env"]["NPM_TOKEN"] == "${{ secrets.NPM_TOKEN }}"
    assert steps[auth_index]["run"] == "printf '//registry.npmjs.org/:_authToken=%s\\n' \"$NPM_TOKEN\" > ~/.npmrc"
    assert steps[preflight_index]["run"] == "bun pm whoami"
    assert auth_index < preflight_index < publish_index


def test_npm_release_publishes_public_package_with_bun(release_yml: dict):
    """The registry tarball is published by bun; no npm CLI anywhere in the
    release path (npm's provenance attestation went with it)."""
    steps = _run_steps(release_yml["jobs"]["publish-npm"])
    publish = next(step for step in steps if "bun publish" in step["run"])

    assert publish["run"] == "bun publish --access public"
    assert not any(step["run"].startswith("npm ") for step in steps)
    assert not any("setup-node" in str(step.get("uses", "")) for step in release_yml["jobs"]["publish-npm"]["steps"])


def test_github_packages_release_routes_the_scope_through_npmrc(release_yml: dict):
    """bun 1.4 ignores package.json ``publishConfig.registry``, and it keeps
    an .npmrc token only for the registry that same file points at (the
    default or a scoped one) -- ``--registry`` on the command line comes too
    late. So the scope route and the token must sit together in ~/.npmrc.
    Locked after the v0.2.0 mirror publish went to npmjs carrying the GitHub
    token and died with "missing authentication" (docs-internal/errors.md
    #24)."""
    steps = _run_steps(release_yml["jobs"]["publish-github-packages"])
    auth = next(step for step in steps if "_authToken" in step["run"])
    assert "@zeenie-ai:registry=https://npm.pkg.github.com/" in auth["run"]
    assert "//npm.pkg.github.com/:_authToken=" in auth["run"]
    assert auth["env"]["NPM_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert any(step["run"] == "bun publish" for step in steps)
    assert not any("publishConfig" in step["run"] for step in steps)


def test_release_dispatch_republishes_an_existing_tag(release_yml: dict):
    """Manual dispatch exists to redo the publish of a tag that already
    exists (a lapsed token, a failed mirror). It must check out that tag --
    a shallow checkout of main has no tags for ``company version sync`` and
    would otherwise pack untagged code -- and let one registry be chosen so
    a registry that already holds the version is not published to again."""
    triggers = release_yml.get("on") or release_yml[True]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["tag"]["required"] is True
    assert set(inputs["registries"]["options"]) == {"both", "npmjs", "github-packages"}
    assert inputs["registries"]["default"] == "both"

    for job_name, skipped_for in (
        ("publish-npm", "github-packages"),
        ("publish-github-packages", "npmjs"),
    ):
        job = release_yml["jobs"][job_name]
        assert job["if"] == (
            f"github.event_name == 'push' || inputs.registries != '{skipped_for}'"
        )
        checkout = next(
            step for step in job["steps"] if "actions/checkout" in str(step.get("uses", ""))
        )
        assert checkout["with"]["ref"] == "${{ inputs.tag || github.ref }}"


@pytest.mark.parametrize("scope", ["@zeenie", "@zeenie-ai"])
def test_preinstall_cleans_scoped_npm_temp_directories(
    preinstall_js_src: str, scope: str
):
    assert repr(scope) in preinstall_js_src
    assert (
        "cleanupTempDirectories(resolve(nodeModules, scope), scopedTempPrefixes)"
        in preinstall_js_src
    )
    assert "prefixes.some((prefix) => name.startsWith(prefix))" in preinstall_js_src


def test_preinstall_does_not_touch_unrelated_unscoped_opencompany_temps(
    preinstall_js_src: str,
):
    assert "const legacyTempPrefixes = ['.machina-']" in preinstall_js_src
    assert (
        "cleanupTempDirectories(nodeModules, legacyTempPrefixes)"
        in preinstall_js_src
    )
    assert "cleanupTempDirectories(nodeModules, scopedTempPrefixes)" not in preinstall_js_src


def test_preinstall_never_removes_current_package_directory(preinstall_js_src: str):
    assert "const currentPackageDir = resolve(__dirname, '..')" in preinstall_js_src
    assert "if (fullPath === currentPackageDir) continue" in preinstall_js_src


def test_preinstall_gates_source_checkouts_to_bun(preinstall_js_src: str):
    """The dev-PM gate keys on ``bunfig.toml`` (committed, excluded from
    the published tarball by the ``files`` allowlist) and on the user agent
    starting with ``bun``. Bun's UA contains the literal ``npm/?``
    substring, so a substring match on ``npm`` would misfire — the gate
    must use a prefix check. A published tarball (no bunfig.toml) never
    trips the gate, whatever installed it.
    """
    assert "bunfig.toml" in preinstall_js_src
    assert "agent.startsWith('bun')" in preinstall_js_src
    assert "pnpm-workspace" not in preinstall_js_src


def test_root_manifest_declares_bun_as_the_dev_package_manager(root_pkg: dict):
    """The ``packageManager`` pin is read by ``oven-sh/setup-bun`` in CI
    and doubles as the read-floor for ``bun.lock`` (the ranged overrides
    force lockfileVersion 3, which older bun cannot parse). The security
    pins that lived in ``pnpm.overrides`` must survive at the top level.
    """
    assert root_pkg.get("packageManager", "").startswith("bun@")
    assert "pnpm" not in root_pkg, (
        "the pnpm config block must not resurface — its overrides moved to "
        "the top-level 'overrides' key and peerDependencyRules has no bun "
        "equivalent"
    )
    overrides = root_pkg.get("overrides", {})
    assert overrides, "top-level overrides (security pins) must exist"
    assert any("@<" in key or "@>=" in key for key in overrides), (
        "the version-ranged override keys (e.g. 'esbuild@<0.28.1') must be "
        "preserved verbatim"
    )
    assert root_pkg.get("workspaces") == ["client", "server/nodejs"], (
        "workspaces must list both member PATHS — pnpm-workspace.yaml is gone "
        "and bun reads only this array"
    )


def test_root_scripts_never_hop_through_npm_or_node(root_pkg: dict):
    """Every root script that reaches another package's script does so via
    ``bun --cwd=<dir> run`` (the ``=`` form — bun 1.4 rejects the
    space-separated spelling), and the lifecycle hooks run their scripts
    under bun: there is no Node on the machine of a ``bun add -g`` user.
    """
    for name, cmd in root_pkg["scripts"].items():
        assert "npm run" not in cmd and "npx " not in cmd, (
            f"root script {name!r} hops through npm: {cmd!r}"
        )
        assert not cmd.startswith("node ") and " node " not in f" {cmd} ", (
            f"root script {name!r} invokes node directly: {cmd!r}"
        )
    for hook in ("preinstall", "postinstall", "preuninstall"):
        assert root_pkg["scripts"][hook].startswith("bun scripts/"), hook
    assert root_pkg["scripts"]["client:start"] == "bun --cwd=client run start"
    assert root_pkg["scripts"]["test:frontend"] == "bun --cwd=client run test"


def test_root_package_declares_bun_engine_and_no_express(root_pkg: dict):
    """``engines`` names bun, the runtime of the launcher and the sidecar;
    the root ``express`` dependency (once a hack so the npm-tarball install
    could run the sidecar's external express) is gone — the sidecar bundle
    inlines it."""
    engines = root_pkg.get("engines", {})
    assert "bun" in engines and "node" not in engines
    assert "express" not in root_pkg.get("dependencies", {})


def test_installers_provision_through_the_shim_not_a_guessed_package_root(root: Path):
    """``bun add -g`` runs no dependency lifecycle scripts, so the installers
    provision explicitly. They must do it through ``company provision``
    (the shim knows its own package root) and never by spelling bun's global
    package directory, which differs by platform and configuration (on
    Windows 1.4 it landed in the user profile, not under BUN_INSTALL)."""
    for rel in ("install.sh", "install.ps1", "cli/terraform/gcp/startup.sh.tftpl"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "provision" in src, rel
        assert "install/global/node_modules" not in src, rel
        assert "scripts/install.js" not in src, rel
        # bun walks up from its (initially empty) global dir to the first
        # package.json; a stray one in $HOME hijacks the install (errors.md
        # 23). Every installer seeds the global dir's own manifest first.
        assert "install/global" in src.replace("\\", "/") and "private" in src, rel
    launcher = (root / "bin" / "cli.js").read_text(encoding="utf-8")
    assert "function provision(" in launcher
    assert "function ensureProvisioned(" in launcher
    assert "cmd === 'provision'" in launcher


def test_launcher_and_install_scripts_run_under_bun(root: Path):
    """The ``company`` bin and the lifecycle scripts carry a bun shebang, so
    a global install runs them on bun (npm's cmd-shim on Windows and the
    symlinked bin on POSIX both honour it)."""
    for rel in ("bin/cli.js", "bin/machina.js", "scripts/install.js", "scripts/preinstall.js", "scripts/postinstall.js"):
        first = (root / rel).read_text(encoding="utf-8").splitlines()[0]
        assert first == "#!/usr/bin/env bun", f"{rel}: {first!r}"


def test_bunfig_pins_the_isolated_linker(root: Path):
    """``linker = "isolated"`` preserves pnpm's phantom-dependency guard
    (symlinked layout); the tsgo typeRoots / vite-env.d.ts workarounds
    asserted above depend on it. bun.lock's configVersion=1 implies the
    same default, but the explicit bunfig makes the choice reviewable.
    """
    src = (root / "bunfig.toml").read_text(encoding="utf-8")
    assert re.search(r'^linker\s*=\s*"isolated"', src, re.MULTILINE), (
        'bunfig.toml must pin linker = "isolated"'
    )


# ---------------------------------------------------------------------------
# postinstall — scripts/install.js
# ---------------------------------------------------------------------------

# Single source of truth for parsing install.js's compileall command line.
# Must match the shape that build.py emits exactly:
#     uv run python -m compileall -q -j 0 <dirs...>
# No -O: runtimes launch python without -O and per PEP 488 only load
# plain .pyc — .opt-1.pyc output would never be used.
_INSTALL_JS_COMPILEALL_RE = re.compile(
    r"""['"]uv\s+run\s+python\s+-m\s+compileall\s+-q\s+-j\s+0\s+([^'"]+)['"]"""
)


def test_install_js_compileall_command_shape(install_js_src: str):
    """End-user ``npm install opencompany`` runs install.js. The
    compileall step must use the same shape as ``company build`` —
    ``uv run python -m compileall -q -j 0`` (plain .pyc, no -O) — so
    cold-start gains apply to the npm-tarball path too.
    """
    assert (
        _INSTALL_JS_COMPILEALL_RE.search(install_js_src) is not None
    ), "install.js must run `uv run python -m compileall -q -j 0 ...`"


def test_server_pyproject_enables_uv_compile_bytecode(root: Path):
    """``[tool.uv] compile-bytecode = true`` must stay set in
    server/pyproject.toml — uv's default is false, which leaves all
    site-package ``.py`` files to compile lazily on the FIRST import
    after a fresh ``uv sync`` (tens of seconds on a post-clean cold
    boot). The compileall step above only covers project source; this
    setting covers ``.venv/``.
    """
    import tomllib

    pyproject = tomllib.loads(
        (root / "server" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject.get("tool", {}).get("uv", {}).get("compile-bytecode") is True, (
        "server/pyproject.toml must set [tool.uv] compile-bytecode = true "
        "(cold-boot bytecode precompilation for site-packages)"
    )


def test_install_js_compileall_paths_match_source_dirs_constant(install_js_src: str):
    """install.js's path list and ``build.COMPILEALL_SOURCE_DIRS`` must
    match exactly. Drift means the npm-tarball install would compile a
    different set of files than ``company build``.
    """
    match = _INSTALL_JS_COMPILEALL_RE.search(install_js_src)
    assert match is not None, "compileall invocation not found in install.js"
    install_paths = tuple(match.group(1).split())
    assert install_paths == build.COMPILEALL_SOURCE_DIRS, (
        "install.js compileall paths drift from COMPILEALL_SOURCE_DIRS:\n"
        f"  install.js: {install_paths!r}\n"
        f"  constant  : {build.COMPILEALL_SOURCE_DIRS!r}"
    )


def test_install_js_compileall_is_non_fatal(install_js_src: str):
    """install.js's compileall call must be wrapped in try/catch with a
    non-fatal warning. A malformed source file in a future commit
    would otherwise fail every user's install — the runtime
    regenerates pyc on first import anyway.
    """
    pattern = re.compile(
        r"try\s*\{[^}]*compileall[^}]*\}\s*catch\s*\([^)]*\)\s*\{[^}]*[Ww]arning[^}]*\}",
        re.DOTALL,
    )
    assert pattern.search(install_js_src) is not None, (
        "compileall call in install.js must be wrapped in a try/catch "
        "with a Warning log so it stays non-fatal"
    )


# ---------------------------------------------------------------------------
# Desktop release workflow — one draft per tag, drafted from the tag message
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def desktop_release_yml(root: Path) -> dict:
    path = root / ".github" / "workflows" / "desktop-release.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_desktop_release_drafts_from_the_tag_before_the_matrix_builds(
    desktop_release_yml: dict,
):
    """``prepare`` creates the one draft the three legs upload into, titled
    and annotated from the tag message; ``finalize`` only undrafts it."""
    jobs = desktop_release_yml["jobs"]
    assert jobs["build"]["needs"] == "prepare"
    assert jobs["finalize"]["needs"] == "build"

    draft = next(
        step for step in jobs["prepare"]["steps"] if "gh release create" in step.get("run", "")
    )
    assert draft["if"] == "github.event_name == 'push'"
    for flag in ("--draft", "--verify-tag", "--notes-file"):
        assert flag in draft["run"]
    assert "git/tags/" in draft["run"]  # the notes are the tag object's message
    assert "gh release view" in draft["run"]  # idempotent on a workflow re-run

    undraft = next(
        step for step in jobs["finalize"]["steps"] if "gh release edit" in step.get("run", "")
    )
    assert "--draft=false" in undraft["run"]


def test_desktop_release_refuses_a_version_that_does_not_match_the_tag(
    desktop_release_yml: dict,
):
    """The tag-triggered desktop build ships the committed desktop version
    (``bun run sync-version`` copies the root package.json; nothing there
    reads the tag), so a forgotten release bump must fail before any upload."""
    steps = desktop_release_yml["jobs"]["build"]["steps"]
    names = [step.get("name", "") for step in steps]
    sync_index = names.index("Sync version")
    guard_index = names.index("Version must match the tag")
    dist_index = next(i for i, step in enumerate(steps) if "bun run dist" in step.get("run", ""))
    assert sync_index < guard_index < dist_index

    guard = steps[guard_index]
    assert guard["if"] == "github.event_name == 'push'"
    assert guard["shell"] == "bash"
    assert "exit 1" in guard["run"]


# ---------------------------------------------------------------------------
# Docker image — toolchain pins shared with CI and the desktop bundle
# ---------------------------------------------------------------------------


def test_docker_image_pins_match_the_bun_and_uv_pins(root: Path, root_pkg: dict):
    """``docker/Dockerfile`` copies bun and uv out of their official images.
    Those tags repeat pins that live elsewhere: bun in the root
    ``packageManager`` (what CI's setup-bun reads) and uv in
    ``desktop/runtimes.json`` (the desktop bundle). A bump in one place must
    move the other, or the image builds with a different toolchain than CI
    and the desktop app.
    """
    dockerfile = (root / "docker" / "Dockerfile").read_text(encoding="utf-8")
    bun = re.search(r"^FROM oven/bun:(\d+\.\d+\.\d+)\S* AS bun$", dockerfile, re.MULTILINE)
    uv = re.search(r"^FROM ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+) AS uv$", dockerfile, re.MULTILINE)
    assert bun and uv, "docker/Dockerfile must copy bun and uv from pinned official images"

    assert f"bun@{bun.group(1)}" == root_pkg["packageManager"]
    runtimes = json.loads((root / "desktop" / "runtimes.json").read_text(encoding="utf-8"))
    assert uv.group(1) == runtimes["uv"]["version"]


def test_docker_port_matches_the_template_port(root: Path):
    """``EXPOSE`` and the compose mapping repeat ``PORT`` from
    ``.env.template`` (the image resolves the real bind at start). A template
    change that left them behind would publish a port nothing listens on.
    """
    from cli.config import _load_env_file

    port = _load_env_file(root / ".env.template")["PORT"]
    dockerfile = (root / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert re.findall(r"^EXPOSE (\d+)$", dockerfile, re.MULTILINE) == [port]

    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    ports = compose["services"]["opencompany"]["ports"]
    assert [mapping.rsplit(":", 1)[1] for mapping in ports] == [port]
