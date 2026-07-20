# Repository guidance

## Releases

- Follow `docs/releasing.md`; releases move from `next` to `main` through the
  Prepare Release and Publish Release workflows, then synchronize back to
  `next` with the generated PR.
- Keep the explicit firmware and container dispatches in Publish Release.
  GitHub suppresses downstream workflow triggers for events created with a
  workflow's `GITHUB_TOKEN`, so creating the GitHub Release does not reliably
  invoke the firmware workflow's `release` trigger.
- Do not update `next` between merging the release PR and running Publish
  Release. Its validation deliberately fails if `next` has moved.
- GitHub CLI authentication and network access may require execution outside a
  restricted sandbox.
