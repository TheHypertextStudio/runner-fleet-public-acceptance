# Development

## Quickstart

This repository has no build step and no test suite of its own. Its only code is
the CI recovery script, and its workflows run on the organization's runners, so
a change is checked locally by confirming the script still parses and the
workflow still matches the bytes recorded in its recovery manifest. These
commands need only Python 3 and `shasum`, with no accounts, sign-ins or package
tokens. Run them from the repository root:

```sh
python3 -c "import ast; ast.parse(open('.github/hypertext/runner_recovery.py').read())"
shasum -a 256 .github/workflows/checks.yml
```

The printed digest must equal the `sha256` recorded for
`.github/workflows/checks.yml` in `.github/hypertext/recovery.json`. Change the
workflow and the manifest together, in the same pull request.

Pull requests do not start the acceptance jobs. They run only from a manual
dispatch on `main`, as the [README](../README.md) explains.

## Maintainers

Commits are formatted by pinned tools: Prettier 3.9.10, Ruff 0.17.2 and the
shared `@thehypertextstudio/format-config` 0.0.1 preset, which is downloaded
from GitHub Packages. A maintainer sets `git config --local user.email
YOU@hypertext.studio`, has the GitHub CLI signed in or a `NODE_AUTH_TOKEN` with
`read:packages` access, and installs the tools and commit hooks from the
repository root:

```sh
python3 .hypertext/formatting/install.py
```

Repeat the command after a formatter pin changes or after creating a checkout.
