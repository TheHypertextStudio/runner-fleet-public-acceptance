# Public runner acceptance

A small public test repository for The Hypertext Studio shared CI fleet. It contains no product code or private operational records.

Every acceptance attempt uses isolated local ARM64 Linux and macOS guests. If local Mac capacity is unavailable, jobs remain queued without allocating a runner or starting build compute. Hosted execution is disabled; the workflow has no hosted fallback selector, and the hosted recovery workflow remains disabled.

Only a manual run of `.github/workflows/checks.yml` on this repository's trusted `main` branch is eligible for local execution. Pull-request jobs are skipped before runner allocation. Forks and other branches cannot receive local runner capacity; exact organization runner restrictions and job guards remain in place.

Hosted fallback would require a separately reviewed routing change and explicit owner authorization for its costs. Changing a repository variable or enabling the existing watchdog alone does not add hosted routing to this workflow.

No deployment, signing, publishing, paid runner or budget increase is part of this repository. The recovery manifest records the exact reviewed workflow bytes.

## Local commit formatting

Prepare the pinned formatting tools and local Git hooks from this repository's root:

```sh
python3 .hypertext/formatting/install.py
```

Repeat this command after updating formatter pins or creating a checkout. Commits
format staged source and configuration while retaining unstaged edits. Setup
prepares dependencies; committing never downloads tools or starts build services.
