# Releases without GitHub Actions

GitHub Releases are published from a validated commit on `main`. GitHub Actions
and automatic Dependabot updates are disabled; no hosted status check or paid
model evaluation is a prerequisite. Record the actual local evidence and its
platform limits instead of claiming an unrun CI matrix.

1. Create a release branch and select a semantic version. Align the Claude
   manifest, marketplace entry, Codex manifest, root `package.json`, lockfile (both
   root entries) and MCP server initialization version. Do not change dependency
   resolutions merely to bump the version.
2. Move the unreleased changes into a versioned changelog entry. Update README
   installation or upgrade guidance and explain compatibility changes, including
   removed hooks. Keep repository About/topics and documentation links current.
3. Run the applicable checks from [CONTRIBUTING.md](../CONTRIBUTING.md):
   deterministic suites, strict manifest/skill/agent validation, syntax checks,
   dependency audit, installation doctor, Codex protocol smoke, real build
   fixtures and extracted Java examples. Record warnings and unavailable checks.
   Behavioral evaluations require a separate explicit request and budget.
4. Commit with the repository's Conventional Commits format, open a PR and
   merge after local validation. Preserve PR and branch-history protections.
   Confirm that the merged tree matches the validated tree.
5. Create an annotated version tag at that exact merged commit and push it.
   Publish with `gh release create <tag> --verify-tag --latest --notes-file <file>`.
   Include change and upgrade notes, prerequisites, actual validation scope and
   a compare link. GitHub supplies source archives; an npm publication is not
   part of this repository's release process because its MCP package is private.
6. Attach a concise validation report identifying the release commit, versions,
   commands/results and limitations. Verify the public release, latest pointer,
   tag target, all version declarations and documentation links. Synchronize
   local `main` and remove the completed release branch.

Keep earlier releases and tags intact. A GitHub Release does not submit the
plugin to the OpenAI directory, change installed host caches or prove model
usefulness. Users must update the installed bundle and restart their host.
