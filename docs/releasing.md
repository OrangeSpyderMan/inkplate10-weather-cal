# Release process

The repository uses `next` as the integration branch and `main` as the stable
release branch. Release merge commits on `main` must be merged back into `next`
so release tags remain in the integration branch's ancestry.

## Automated release

1. Run **Prepare Release** from the Actions page and enter a stable SemVer tag,
   such as `v3.2.0`.
2. Review and merge the generated `next` to `main` pull request using a merge
   commit after its required checks pass.
3. Run **Publish Release** with the same version.
4. Confirm the dispatched firmware and container workflows pass.
5. Review and merge the generated synchronization pull request into `next`.

The publish workflow is safe to retry when the release tag already points to
the current `main`. It will refuse to move an existing tag.

The explicit firmware and container workflow dispatches in **Publish Release**
are intentional. The GitHub Release is created with the workflow's
`GITHUB_TOKEN`, and GitHub does not start new workflow runs from events created
by that token. In particular, the firmware workflow's `release` trigger does
not replace the explicit dispatch. Retrying **Publish Release** must also
redispatch the builds so missing assets or images can be recovered.

Do not merge new work into `next` between merging the release PR and running
the publish workflow. Publication verifies that current `main` is the exact
merge commit of the matching release PR.

The synchronization PR starts from the released `main` commit and updates
`.github/release-sync`. The marker gives GitHub a visible file change even when
the trees on `main` and `next` would otherwise be identical.

## Bootstrap

GitHub only exposes a manually dispatched workflow after that workflow exists
on the default branch. The first promotion containing these workflows must
therefore be opened and merged manually. Subsequent releases can use the
automated process.

## Hotfixes

Create hotfixes from `main`, release them normally, and merge the generated
synchronization PR into `next`. Do not rebase the published `next` branch.
