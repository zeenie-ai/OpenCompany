"""Config-file assertions for the release-build pipeline.

These tests guard contracts that ``test_build_compile_pipeline.py``
can't cover from Python — the configuration of sibling tools (Vite,
TypeScript, esbuild, GitHub Actions, ``scripts/install.js``).

Reused infrastructure (no path strings duplicated across tests):

  ``conftest.workspace_members``     → name → Path map from ``pnpm list``
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
# Node.js sidecar — package.json & .gitignore
# ---------------------------------------------------------------------------


def test_sidecar_start_runs_compiled_bundle(sidecar_pkg: dict):
    """``npm start`` runs the esbuild-bundled output, not interpreted
    TypeScript via tsx. tsx adds ~500ms-1s of interpreter startup that
    the bundle eliminates.
    """
    start = sidecar_pkg["scripts"]["start"]
    assert (
        start == "node dist/index.js"
    ), f"sidecar start must be `node dist/index.js`, got {start!r}"
    assert "tsx" not in start


def test_sidecar_dev_keeps_tsx_for_hot_reload(sidecar_pkg: dict):
    """``npm run dev`` keeps tsx watch — the bundle workflow is too slow
    for local iteration. Only production ``start`` uses the bundle.
    """
    dev = sidecar_pkg["scripts"]["dev"]
    assert "tsx" in dev and "watch" in dev


@pytest.mark.parametrize(
    "flag",
    [
        "esbuild",
        "src/index.ts",
        "--bundle",
        "--platform=node",
        "--target=node22",
        "--format=esm",
        "--packages=external",
        "--outfile=dist/index.js",
    ],
)
def test_sidecar_build_script_carries_required_esbuild_flag(
    sidecar_pkg: dict, flag: str
):
    """Each esbuild flag in the sidecar build script is load-bearing.

    - ``--bundle`` — concat the executor's own TS into one file.
    - ``--platform=node`` — preserve Node built-in resolution.
    - ``--target=node22`` — match ``engines.node`` in the same file.
    - ``--format=esm`` — package.json ``type=module`` requires ESM.
    - ``--packages=external`` — keep Express in node_modules; only the
      executor's own TS is concatenated, so patch flow stays intact.
    - ``--outfile=dist/index.js`` — ``start`` and ``main`` read this
      exact path; mismatch breaks the runtime.
    """
    cmd = sidecar_pkg["scripts"]["build"]
    assert flag in cmd, f"sidecar build script missing {flag!r}: {cmd}"


def test_sidecar_engines_match_esbuild_target(sidecar_pkg: dict):
    """``--target=node22`` and ``engines.node`` must agree. Bumping one
    without the other would silently produce code that runs on a Node
    version the package claims it doesn't support (or vice versa).
    """
    engines_node = sidecar_pkg.get("engines", {}).get("node", "")
    assert "22" in engines_node, (
        f"engines.node must declare ≥22 to match esbuild --target=node22, "
        f"got {engines_node!r}"
    )


def test_sidecar_main_field_points_at_compiled_output(sidecar_pkg: dict):
    """``main`` is what ``import 'opencompany-nodejs-executor'`` resolves
    to — the compiled bundle, not the TS source.
    """
    assert sidecar_pkg.get("main") == "dist/index.js"


def test_sidecar_esbuild_is_dev_only(sidecar_pkg: dict):
    """esbuild builds the bundle; the runtime never touches it. It must
    be a devDependency so a ``--omit=dev`` install doesn't ship it.
    """
    assert "esbuild" in sidecar_pkg.get("devDependencies", {})
    assert "esbuild" not in sidecar_pkg.get("dependencies", {})


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


def test_client_typecheck_uses_tsgo(client_pkg: dict):
    """The fast type-check (``pnpm run typecheck``) goes through tsgo
    (Microsoft's Go-port of tsc, ~5x faster). The slower tsc fallback
    stays available as ``typecheck:tsc`` in case tsgo regresses on a
    dev release.
    """
    scripts = client_pkg["scripts"]
    assert (
        scripts.get("typecheck") == "tsgo --noEmit"
    ), f"client typecheck must be tsgo, got {scripts.get('typecheck')!r}"
    assert scripts.get("typecheck:tsc") == "tsc --noEmit", (
        "tsc fallback (typecheck:tsc) must remain available so a tsgo "
        "regression has a one-line revert path"
    )


def test_tsgo_devdependency_is_pinned_exactly(client_pkg: dict):
    """tsgo ships breaking changes between dev releases. A ``^`` or
    ``~`` range would silently shift the type-check gate on each
    install. Pin the exact version so upgrades are explicit.
    """
    version = client_pkg.get("devDependencies", {}).get("@typescript/native-preview")
    assert (
        version is not None
    ), "@typescript/native-preview must be in client devDependencies"
    assert not version.startswith(
        ("^", "~", ">=", ">", "<")
    ), f"tsgo must be pinned exactly (no range prefix), got {version!r}"


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
    where to find ``@types/*`` packages — needed because pnpm's
    symlinked ``node_modules`` layout can confuse auto-discovery.
    """
    src = (client_dir / "tsconfig.json").read_text(encoding="utf-8")
    assert not re.search(r'^\s*"baseUrl"', src, re.MULTILINE), (
        "TS 7 removed baseUrl — remove it; paths still resolve relative "
        "to tsconfig.json without it"
    )
    assert re.search(
        r'"typeRoots":\s*\[\s*"\./node_modules/@types"\s*\]',
        src,
    ), "typeRoots must point at ./node_modules/@types for tsgo + pnpm"
    # The path alias must survive the baseUrl removal.
    assert re.search(
        r'"@/\*":\s*\["\./src/\*"\]', src
    ), "the `@/*` -> `./src/*` alias must remain configured"


def test_vite_env_dts_references_google_maps(client_dir: Path):
    """``@types/google.maps`` exposes a global ``google.maps`` namespace
    (no module export). tsgo's auto-discovery doesn't pick it up
    reliably through pnpm's symlinks, so the canonical Google-recommended
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


def test_predeploy_typecheck_step_routes_through_pnpm_script(predeploy_yml: dict):
    """The CI typecheck step must invoke the npm script (which routes
    through tsgo) rather than calling ``tsc`` directly. Keeps the
    fast-vs-fallback choice in one place — ``client/package.json`` —
    so devs can swap tools without editing the workflow.
    """
    job = predeploy_yml["jobs"]["build-and-lint"]
    step = _step_by_name(job, "TypeScript check")
    assert step is not None, "predeploy.yml must define a `TypeScript check` step"
    cmd = step.get("run", "")
    assert (
        "run typecheck" in cmd
    ), f"predeploy.yml typecheck step must call `pnpm ... run typecheck`, got {cmd!r}"
    assert (
        CLIENT_PKG_NAME in cmd
    ), f"predeploy.yml typecheck step must filter to {CLIENT_PKG_NAME}, got {cmd!r}"
    assert "tsc --noEmit" not in cmd, (
        "predeploy.yml still calls `tsc --noEmit` directly — switch to "
        "the typecheck script so tsgo is used"
    )


# ---------------------------------------------------------------------------
# Release publication — package.json, release.yml, preinstall.js
# ---------------------------------------------------------------------------


def _run_steps(job: dict) -> list[dict]:
    return [step for step in job.get("steps", []) if "run" in step]


def test_root_package_uses_public_zeenie_scope(root_pkg: dict):
    assert root_pkg["name"] == "@zeenie/opencompany"
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


def test_npm_release_authenticates_and_checks_scope_before_publish(
    release_yml: dict,
):
    steps = _run_steps(release_yml["jobs"]["publish-npm"])
    preflight_index = next(
        i for i, step in enumerate(steps) if "npm whoami" in step["run"]
    )
    publish_index = next(
        i for i, step in enumerate(steps) if "npm publish" in step["run"]
    )
    preflight = steps[preflight_index]

    assert "npm access list packages @zeenie --json" in preflight["run"]
    assert preflight["env"]["NODE_AUTH_TOKEN"] == "${{ secrets.NPM_TOKEN }}"
    assert preflight_index < publish_index


def test_npm_release_publishes_public_package_with_provenance(release_yml: dict):
    steps = _run_steps(release_yml["jobs"]["publish-npm"])
    publish = next(step for step in steps if "npm publish" in step["run"])

    assert publish["run"] == "npm publish --access public --provenance"
    assert publish["env"]["NODE_AUTH_TOKEN"] == "${{ secrets.NPM_TOKEN }}"


def test_github_packages_release_keeps_github_owner_scope(release_yml: dict):
    steps = _run_steps(release_yml["jobs"]["publish-github-packages"])
    configure = next(
        step for step in steps if "pkg.name = '@zeenie-ai/opencompany'" in step["run"]
    )

    assert "https://npm.pkg.github.com" in configure["run"]
    assert any(step["run"] == "npm publish" for step in steps)


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


# ---------------------------------------------------------------------------
# postinstall — scripts/install.js
# ---------------------------------------------------------------------------

# Single source of truth for parsing install.js's compileall command line.
# Must match the shape that build.py emits exactly:
#     uv run python -O -m compileall -q -j 0 <dirs...>
_INSTALL_JS_COMPILEALL_RE = re.compile(
    r"""['"]uv\s+run\s+python\s+-O\s+-m\s+compileall\s+-q\s+-j\s+0\s+([^'"]+)['"]"""
)


def test_install_js_compileall_command_shape(install_js_src: str):
    """End-user ``npm install opencompany`` runs install.js. The
    compileall step must use the same shape as ``company build`` —
    ``uv run python -O -m compileall -q -j 0`` — so cold-start gains
    apply to the npm-tarball path too.
    """
    assert (
        _INSTALL_JS_COMPILEALL_RE.search(install_js_src) is not None
    ), "install.js must run `uv run python -O -m compileall -q -j 0 ...`"


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
