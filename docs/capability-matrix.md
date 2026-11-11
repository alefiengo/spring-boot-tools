# Host capabilities and evidence

This matrix describes shipped registrations and executable boundaries. A
registration or passing installation check does not establish model skill
selection, review quality, or live task success.

| Capability | Claude Code | Codex local executor | Evidence and limits |
| --- | --- | --- | --- |
| Shared skills | Plugin skills | Same `skills/` content via compatibility manifest | Manifests and skill files establish packaging. Model selection and task efficacy are unmeasured by the doctor. |
| Seven MCP tools | Root `.mcp.json` | Separate `codex/mcp.json` through `codex/run.mjs` and `codex/launch.mjs` | Both use `mcp-server/index.js` and the dependencies installed at the plugin root. Entrypoint/dependency resolution is weaker evidence than initialize/list-tools or a tool invocation. |
| Project discovery and builds | MCP and hooks share Python runtime | MCP delegates to the same runtime; verification is explicit | `runtime/project_runtime.py` owns discovery, command construction, locking and reuse. Optional doctor discovery calls only its discover action; it never runs a build, and warns with `discovered_root` when the intended project is part of a larger reactor and with `stale_dependencies` when a leftover `mcp-server/node_modules` shadows the root install. |
| Specialist agents | `agents/` contains Claude registrations | Claude agents are not registered as Codex agents | Reading specialist guidance is possible but does not establish independent agent execution. |
| Edit guards and verification hooks | `hooks/hooks.json` registers two pre-edit guards, post-edit migration lint and Stop verification | Explicit empty manifest hooks disable Claude hook discovery | Claude event execution is host-specific. No post-edit marker or build is required; Stop uses current fingerprints. Codex requires explicit verification tools. |
| Java LSP | Optional `.lsp.json` registration for externally installed `jdtls` | No Java LSP registration supplied | Neither packaging nor doctor checks prove a running language server; the doctor does not launch or require it. |
| Options and workspace | Claude-specific project/user-option placeholders | Explicit bundle environment handoff; MCP cwd selects application | Root Claude MCP config is not the Codex adapter. A running session must be restarted to change its MCP cwd. |
| Initialization | Explicit MCP tool request | Same shared tool/runtime | Generation uses network when requested through that tool. The offline doctor never invokes initialization or fetches metadata. |
| Installation diagnostics | Local doctor can inspect bundle registration | Same local doctor; checks `SBT_PLUGIN_ROOT` handoff | Text/JSON, offline, no writes, no host launches. It checks a supplied checkout, not installed host caches or account configuration. |

## Version and verification evidence

The doctor checks agreement between Claude and Codex plugin versions, the
marketplace entry, MCP package/lockfile versions, and the MCP server's version
literal. These are bundle consistency checks. They do not query a registry,
establish the latest release, or prove that a host cached the same version.
Its prerequisites are Linux/macOS, Node >=20.3 and Python >=3.11; the doctor
itself needs Python 3.11+ before it can report anything. Python/runtime code
uses the standard library; the Node MCP dependencies must be installed
separately. Application JDK/build dependencies depend on the application and
are not probed by executing its wrappers.

The existing [Codex adapter evidence](codex.md#validation-evidence) records
Codex CLI **0.166.0** for its compatibility-manifest/environment behavior and
**0.167.0** for the optional CLI protocol check. That version-specific evidence is not a
promise about later CLI versions, desktop launches, or web-only execution.
There is no pinned Claude Code version established by the doctor; installed
host compatibility and event execution need separate host verification.

| Check | What it establishes | What it leaves unmeasured |
| --- | --- | --- |
| `scripts/install-doctor.py` | Offline prerequisites, selected bundle registration/path/version consistency, ESM import target resolution and file existence including `/stdio`, optional shared-runtime project root comparison | Host loading, dependency imports, actual MCP startup, Java/build viability, installed cache alignment, model behavior |
| `python3 -B -m unittest discover -s tests/install-doctor -v` | Controlled missing/old executables, missing dependencies, malformed JSON/registration, relocation, workspace selection, bounded probes and fixture side-effect invariance | A different machine's installation, application compilation, production behavior |
| Existing `bash tests/codex/test-basic.sh` | Deterministic adapter/launcher and real local MCP protocol fixtures, as described in Codex documentation | Live model skill selection or independent specialist reviews |
| Optional existing `python3 tests/codex/cli-smoke.py` | Isolated CLI plugin/protocol inventory and application discovery; starts an empty thread without a model turn | Model efficacy; it writes temporary host configuration and is separate from the read-only doctor |
| Behavioral evaluations | Model responses and criterion judgments for the evaluated cases when explicitly run | General efficacy outside those cases; no such calls are part of doctor validation |

These commands name separate evidence layers; listing a command does not claim
it ran for a particular change. Run the applicable local checks and the full
suite before claiming them passed.
Focused doctor checks do not imply those broader checks passed.
