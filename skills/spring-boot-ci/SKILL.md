---
name: spring-boot-ci
description: >
  CI pipelines for Spring Boot services: complete GitHub Actions and GitLab CI
  templates (dependency caching, mvn verify, Testcontainers, image build and
  push tagged by SHA), pipeline-integrated scans (secrets, outdated
  dependencies, SBOM), and 12-factor environment handling. Use when asked to
  set up CI, write a GitHub Actions workflow or .gitlab-ci.yml, speed up a
  pipeline, add scans to CI, or fix a failing pipeline.
---

# CI for Spring Boot

## Verification and publication are separate responsibilities

Adapt templates to the repository wrapper, modules, Java target and declared integration dependencies. Verification on a PR gets read-only permissions and no deployment credentials. Keep publication/deployment in a distinct trusted-branch or manually approved job, with protected environments and narrowly scoped credentials. Do not infer permission to publish from a request to repair CI.

Build once per source state and share immutable reports/artifacts with downstream jobs where practical. Gate publication on required unit/integration/contract verification; keep load experiments opt-in with a selected environment, resource limit and stop criteria. Avoid suppressing verification failures with `continue-on-error`.

## What the pipeline does, in order

1. **Verify**: `./mvnw verify` (unit + integration with Testcontainers); configure Failsafe or the appropriate Gradle integration task so integration tests actually run.
2. **Scan**: secrets in history, outdated/vulnerable dependencies, SBOM.
3. **Build image**: only on `main` (and `develop` for integration
   environments), tagged by SHA + branch.
4. **Push**: registry of the deployment target.

Fail fast at step 1; never publish an image from a failing pipeline.

## GitHub Actions

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "25"
          cache: maven          # caches ~/.m2 between runs

      - name: Verify (unit + Testcontainers integration)
        run: ./mvnw -B verify

      # Testcontainers works out of the box: ubuntu-latest has Docker.
      # For self-hosted runners, mount /var/run/docker.sock and ensure the
      # runner user is in the docker group.

  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # gitleaks needs full history

      - name: Secrets in history
        uses: gitleaks/gitleaks-action@v2

      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "25"
          cache: maven

      - name: Outdated dependencies
        run: ./mvnw -B versions:display-dependency-updates || true

      - name: SBOM
        uses: anchore/sbom-action@v0
        with:
          format: spdx-json
          upload-artifact: true

  image:
    needs: [verify, scan]
    if: github.ref == 'refs/heads/main' || github.ref == 'refs/heads/develop'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: |
            ghcr.io/${{ github.repository }}:${{ github.sha }}
            ghcr.io/${{ github.repository }}:${{ github.ref_name }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

### Rules

- **`cache: maven`** in `setup-java` is the single highest-impact line:
  dependency download dominates pipeline time otherwise.
- **`fetch-depth: 0`** only for the scan job (gitleaks needs history);
  the verify job keeps the shallow default.
- Image tags: **always the commit SHA** (immutable, traceable), plus the
  branch name for convenience. Never `latest` as a deploy reference.
- `GITHUB_TOKEN` for `ghcr.io` — no personal tokens in the pipeline.

## GitLab CI

```yaml
# .gitlab-ci.yml
stages: [test, publish]

default:
  image: maven:3.9-eclipse-temurin-25

variables:
  MAVEN_OPTS: "-Dmaven.repo.local=$CI_PROJECT_DIR/.m2/repository"
  GIT_DEPTH: 0                 # gitleaks needs history; override per job if it hurts

cache:
  key: maven-$CI_PROJECT_ID
  paths:
    - .m2/repository

verify:
  stage: test
  script:
    - ./mvnw -B verify
  # Testcontainers needs Docker; use a runner with docker:dind service or
  # a runner with the Docker socket mounted.
  services:
    - docker:27-dind
  variables:
    DOCKER_HOST: tcp://docker:2376
    DOCKER_TLS_CERTDIR: "/certs"
    DOCKER_TLS_VERIFY: "1"
    DOCKER_CERT_PATH: "$DOCKER_TLS_CERTDIR/client"

secrets-history:
  stage: test
  image:
    name: ghcr.io/gitleaks/gitleaks:latest
    entrypoint: [""]
  script:
    - gitleaks detect --source . --report-path gitleaks-report.json --exit-code 1

dependencies-check:
  stage: test
  script:
    - ./mvnw -B versions:display-dependency-updates || true

image-build:
  stage: publish
  needs: [verify, secrets-history]
  rules:
    - if: '$CI_COMMIT_BRANCH == "main" || $CI_COMMIT_BRANCH == "develop"'
  image: docker:27
  services:
    - docker:27-dind
  variables:
    DOCKER_HOST: tcp://docker:2376
    DOCKER_TLS_CERTDIR: "/certs"
    DOCKER_TLS_VERIFY: "1"
    DOCKER_CERT_PATH: "$DOCKER_TLS_CERTDIR/client"
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA -t $CI_REGISTRY_IMAGE:$CI_COMMIT_BRANCH .
    - printf "%s" "$CI_REGISTRY_PASSWORD" | docker login -u "$CI_REGISTRY_USER" --password-stdin "$CI_REGISTRY"
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_BRANCH
```

### Rules

- **`MAVEN_OPTS` with `$CI_PROJECT_DIR/.m2`** + the `cache:` block is what
  makes Maven pipelines fast; without redirecting the repo location, the
  default `~/.m2` is not cached.
- A privileged DinD runner must share `/certs/client` between service and job containers for TLS. Configure this in the runner, not only in the YAML.
- **DinD (`docker:27-dind`) or socket-mounted runner** is required for
  Testcontainers. On a socket-mounted runner, drop the DinD service and
  remove `DOCKER_HOST`.
- `GIT_DEPTH: 0` globally is the simple option; if repos are huge, set it
  only on the secrets job (`GIT_DEPTH: 0` per-job override).

## Environment handling (12-factor)

- The pipeline builds **one image, environment-agnostic**. Profiles and
  environment values arrive at deploy time via env vars
  (`SPRING_PROFILES_ACTIVE`, `SPRING_DATASOURCE_URL`, secrets from the
  platform's secret store).
- CI secrets (registry credentials) live in GitHub Actions secrets /
  GitLab CI/CD variables — **never** in the repo, never baked into the image.
- Config files in the repo (`application.yml`) contain structure and safe
  defaults only; anything that varies per deployment is `${ENV_VAR}`.

Pin third-party CI actions/images by reviewed release or digest in the adopted pipeline; the broad tags above are illustrative. Maven version-update reports are informational, not vulnerability scans. Add the organization's dependency vulnerability scanner as a required gate, with its failure policy and maintained vulnerability feed. If GHCR rejects a repository name containing uppercase characters, normalize the image name to lowercase before assigning tags.

## Scaling down CI noise

- Run the dependency/scan jobs **nightly or weekly**, not per push
  (schedule trigger / `rules: - if: $CI_PIPELINE_SOURCE == "schedule"`),
  unless the repo changes dependencies frequently.
- Parallelize `verify` and `scan` — they are independent.
- For local experiments only, Testcontainers reuse requires `testcontainers.reuse.enable=true` in `~/.testcontainers.properties` and `.withReuse(true)` on the container. Do not enable reuse in CI; benchmark build and container startup separately.

## References

- [GitLab Docker-in-Docker with TLS](https://docs.gitlab.com/ci/docker/using_docker_build/)
- [Testcontainers reusable containers](https://java.testcontainers.org/features/reuse/)
