import pathlib
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class ReleaseWorkflowTests(unittest.TestCase):
    def create_repo(self):
        temporary_dir = tempfile.TemporaryDirectory()
        repo = pathlib.Path(temporary_dir.name)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.name", "Test User")
        git(repo, "config", "user.email", "test@example.com")
        return temporary_dir, repo

    def commit_file(self, repo, path, content, message):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git(repo, "add", path)
        git(repo, "commit", "-q", "-m", message)

    def assert_is_ancestor(self, repo, ancestor, descendant):
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repo,
            check=True,
        )

    def test_publish_workflow_opens_next_sync_from_tagged_release_commit(self):
        temporary_dir, repo = self.create_repo()
        version = "v3.2.0"
        with temporary_dir:
            self.commit_file(repo, "app.txt", "released\n", "initial release")
            git(repo, "checkout", "-q", "-b", "next")
            self.commit_file(repo, "app.txt", "next work\n", "next work")

            git(repo, "checkout", "-q", "main")
            git(repo, "merge", "--no-ff", "next", "-m", f"Prepare {version} release")
            release_commit = git(repo, "rev-parse", "HEAD")
            git(repo, "tag", "-a", version, "-m", version)

            sync_branch = f"chore/sync-{version[1:]}-to-next"
            git(repo, "checkout", "-q", "-B", sync_branch)
            self.commit_file(
                repo,
                ".github/release-sync",
                textwrap.dedent(
                    f"""\
                    version: {version}
                    main_commit: {release_commit}
                    synced_at: 2026-06-17
                    """
                ),
                f"chore: sync {version} release back to next",
            )

            git(repo, "checkout", "-q", "next")
            git(repo, "merge", "--no-ff", sync_branch, "-m", f"Sync {version}")

            self.assert_is_ancestor(repo, "main", "next")
            self.assert_is_ancestor(repo, version, "next")
            self.assertEqual(git(repo, "rev-list", "-n", "1", version), release_commit)
            self.assertEqual(
                (repo / ".github" / "release-sync").read_text(),
                textwrap.dedent(
                    f"""\
                    version: {version}
                    main_commit: {release_commit}
                    synced_at: 2026-06-17
                    """
                ),
            )

    def test_release_workflows_dispatch_versioned_builds_and_sync_next(self):
        publish_workflow = (REPO_ROOT / ".github/workflows/publish-release.yml").read_text()
        prepare_workflow = (REPO_ROOT / ".github/workflows/prepare-release.yml").read_text()

        self.assertIn("gh workflow run firmware.yml", publish_workflow)
        self.assertIn('if [ "$RELEASE_CREATED" = true ]', publish_workflow)
        self.assertIn("Firmware build was triggered by publishing", publish_workflow)
        self.assertIn("--ref \"$VERSION\"", publish_workflow)
        self.assertIn("-f release_tag=\"$VERSION\"", publish_workflow)
        self.assertIn("gh workflow run container-publish.yml", publish_workflow)
        self.assertIn("git checkout -B \"$branch\"", publish_workflow)
        self.assertIn("git push --force-with-lease origin \"$branch\"", publish_workflow)
        self.assertIn("--base next", publish_workflow)
        self.assertIn("--head \"$branch\"", publish_workflow)
        self.assertIn("Merge this PR using a merge commit", prepare_workflow)

    def test_container_workflow_embeds_shared_version_manifest(self):
        publish_workflow = (
            REPO_ROOT / ".github/workflows/container-publish.yml"
        ).read_text()
        ci_workflow = (
            REPO_ROOT / ".github/workflows/docker-image.yml"
        ).read_text()
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()

        for workflow in (publish_workflow, ci_workflow):
            self.assertIn("fetch-depth: 0", workflow)
            self.assertIn("bin/generate_version_manifest.py", workflow)
        self.assertIn("COPY --chown=${USERNAME}:${USERNAME} ./.version.json", dockerfile)
        self.assertNotIn("ENV INKPLATE_VERSION=", dockerfile)

    def test_container_workflows_build_only_pillow_image(self):
        publish_workflow = (
            REPO_ROOT / ".github/workflows/container-publish.yml"
        ).read_text()
        ci_workflow = (
            REPO_ROOT / ".github/workflows/docker-image.yml"
        ).read_text()
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()

        self.assertNotIn('suffix: "-pillow"', publish_workflow)
        self.assertNotIn("matrix.target", publish_workflow)
        self.assertNotIn("matrix.target", ci_workflow)
        self.assertNotIn(" AS full", dockerfile)
        self.assertNotIn(" AS pillow", dockerfile)
        self.assertIn("PIP_ROOT_USER_ACTION=ignore", dockerfile)
        self.assertIn(
            'org.opencontainers.image.variant="pillow"',
            dockerfile,
        )
        pillow_requirements = (
            REPO_ROOT / "server/requirements-pillow.txt"
        ).read_text()
        common_requirements = (
            REPO_ROOT / "server/requirements-common.txt"
        ).read_text()
        self.assertIn("rough==", pillow_requirements)
        self.assertIn("Pillow==", common_requirements)
        self.assertNotIn("selenium==", common_requirements)
        self.assertFalse(
            (REPO_ROOT / "server/requirements-firefox.txt").exists()
        )
