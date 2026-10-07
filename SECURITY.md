# Security policy

Aelix Analytics is preparing its first beta. No version has been published yet: there is no package on an index, no
tag and no release, and the wheel is built from a checkout ([CHANGELOG](CHANGELOG.md)).

## Supported versions

| Version | Supported |
|---|---|
| The latest commit on `main` | Yes, until the first beta |
| Earlier commits, and wheels built from them | No: update to the latest `main` |

A fix lands on `main` and is not backported. Which versions receive security fixes after the first beta is decided
with that release and will be written here. Where the code has been installed and run — Python, operating systems,
aelix versions, install routes — is in the [support table](docs/SUPPORT.md).

## Reporting a vulnerability

**Do not report a vulnerability in a public Issue, pull request or discussion, and do not put its details in a
commit.**

Report it through GitHub's private vulnerability reporting: on the repository's **Security** tab, choose **Report a
vulnerability**. The report and the conversation about it stay private between you and the maintainer until an
advisory is published.

The repository is private for now, and private vulnerability reporting is turned on when it becomes public. Until
then, contact the maintainer, [@handochan](https://github.com/handochan), through GitHub, and keep the details out of
Issues, pull requests and commits: in a private repository every collaborator can read them.

## What to include

- What someone can do through the vulnerability, and what they need for it: for example a project, a data file, a
  script or a note that a person opens, an aelix session, or a Workspace folder that is shared.
- The affected part (see Scope below) and its version: the commit of a checkout, or the version of the installed
  wheel. A run records the kit that ran it as `env.kit_version` in its `runs/<id>/run.json`.
- How it was installed: the wheel with `uv tool install`, the wheel in aelix's environment, or a checkout
  (`bin/analytics`, or the extension file given to aelix). The operating system, the Python version and the aelix
  version.
- Steps to reproduce it with synthetic data. Do not send real data, credentials or session logs.
- What you expected and what happened, and a fix if you have one.

## What to expect

- The maintainer acknowledges the report in its private thread, tries to reproduce it, and tells you whether it is
  accepted as a vulnerability.
- An accepted vulnerability is fixed on `main`. Once the fix is on `main`, a GitHub security advisory describing it is
  published.
- The project has one maintainer, and no response or fix time is promised.

## Scope

In scope is the code in this repository:

- the kit: the commands of the `analytics` command line, the SDK (`import aelix_analytics as analytics`), run
  recording, and the Workspace HTML it renders (`src/aelix_analytics/`);
- the aelix extension: its tools, the `/analytics` command and its hooks (`extension/aelix_analytics.py`, installed as
  `aelix_analytics.extension`);
- the launchers: the installed `analytics` and the checkout's `bin/analytics`;
- the evaluation and measurement tools under `tools/` (the model evaluation harness, the usage report, the evaluation
  fixture generator and the others).

Out of scope:

- aelix itself: the host, its extension loader, its own tools and its model providers. Report those to aelix's project;
- the analysis code that people or agents write and run in a project, and the data it reads;
- the libraries an analysis project installs (DuckDB, Polars, matplotlib, Plotly and others) and uv. Report those to
  their projects; a way in which the kit uses them unsafely is in scope.

Documented behaviour is not a vulnerability by itself. Aelix Analytics is not a sandbox: `analytics_run` runs the code
it is given with the user's permissions, aelix does not ask before an extension tool runs, and the Workspace shows data
previews and logs as they are ([README](README.md#한계), [the current contract, G](docs/CONTRACTS.md#g-약속하지-않는-것)).
A way around a boundary the documents do promise is in scope ([the current contract, E](docs/CONTRACTS.md#e-계산-규칙)),
for example: a query through `analytics_query` that is not read-only or that reaches files outside the project's
`data/`; a project activated from the working directory alone without being on the trust list; or text that people or
scripts recorded — a note, a title, a column name, a path — that the Workspace runs as HTML or script instead of
showing it as text.
