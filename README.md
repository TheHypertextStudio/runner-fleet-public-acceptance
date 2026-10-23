# Public runner acceptance

A small public test repository for The Hypertext Studio shared CI fleet. It contains no product code or private operational records.

The manual main-branch workflow verifies isolated local ARM64 Linux and macOS guests. A single bounded hosted retry verifies the same test on GitHub-hosted ARM64 when local capacity is unavailable. The recovery workflow and immutable manifest are generated from the reviewed source after GitHub assigns the real repository ID.

Only `.github/workflows/checks.yml` on the main branch is eligible for local execution. Public fork and pull-request checks must not receive local runner capacity. Existing organization runner restrictions remain exact.

No deployment, signing, publishing, paid runner or budget increase is part of this repository.
