# Public runner acceptance

Hosted execution is disabled. Every attempt waits for local Mac capacity. Re-enabling hosted recovery requires explicit owner approval, setting `HYPERTEXT_HOSTED_CI_ENABLED` to `true`, and enabling the recovery workflow; it never resumes automatically.

A small public test repository for The Hypertext Studio shared CI fleet. It contains no product code or private operational records.

The manual main-branch workflow verifies isolated local ARM64 Linux and macOS guests. A single bounded hosted retry verifies the same test on GitHub-hosted ARM64 when local capacity is unavailable. The recovery workflow and immutable manifest are generated from the reviewed source after GitHub assigns the real repository ID.

Only `.github/workflows/checks.yml` on the main branch is eligible for local execution. Public fork and pull-request checks must not receive local runner capacity. Existing organization runner restrictions remain exact.

No deployment, signing, publishing, paid runner or budget increase is part of this repository.

## Local commit formatting

Prepare the pinned formatting tools and local Git hooks from this repository's root:

```sh
python3 .hypertext/formatting/install.py
```

Repeat this command after updating formatter pins or creating a checkout. Commits
format staged source and configuration while retaining unstaged edits. Setup
prepares dependencies; committing never downloads tools or starts build services.
