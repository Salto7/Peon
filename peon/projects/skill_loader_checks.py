"""Skill loader: tokenizer, profiles, discovery, diagnostics."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from orchestrator.skills.manifest import (
    ParseProfile,
    SkillParseError,
    parse_skill_manifest,
    tokenize_allowed_tools,
)
from orchestrator.skills.model import parse_skill_md
from orchestrator.skills.provision.linter import SkillLinter
from orchestrator.skills.registry_scan import (
    discover_skill_candidates,
    skill_tree_fingerprint,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "orchestrator" / "skills" / "fixtures"


class SkillLoaderChecks(SimpleTestCase):
    def test_tokenize_balanced_parens_and_commas(self) -> None:
        tokens = tokenize_allowed_tools("Bash(git diff *) Read,Write")
        self.assertEqual(tokens, ("Bash(git diff *)", "Read", "Write"))

    def test_tokenize_list_form(self) -> None:
        tokens = tokenize_allowed_tools(["Bash", "Read(file.md)", "Edit"])
        self.assertEqual(tokens, ("Bash", "Read(file.md)", "Edit"))

    def test_fixture_parens_tools(self) -> None:
        path = _FIXTURES / "allowed-tools-parens" / "SKILL.md"
        manifest = parse_skill_manifest(path, profile=ParseProfile.AGENTSKILLS)
        self.assertEqual(
            manifest.allowed_tools,
            ("Bash(git diff *)", "Read", "Write"),
        )

    def test_fixture_list_tools(self) -> None:
        path = _FIXTURES / "list-tools" / "SKILL.md"
        manifest = parse_skill_manifest(path, profile=ParseProfile.CLAUDE_CODE)
        self.assertEqual(manifest.allowed_tools, ("Bash", "Read", "Edit"))

    def test_claude_extensions_preserved(self) -> None:
        path = _FIXTURES / "claude-extensions" / "SKILL.md"
        manifest = parse_skill_manifest(path, profile=ParseProfile.CLAUDE_CODE)
        self.assertEqual(manifest.when_to_use, "When testing Claude extensions")
        self.assertEqual(manifest.argument_hint, "[target]")
        self.assertEqual(manifest.arguments, ("target",))
        self.assertEqual(manifest.context, "fork")
        self.assertEqual(manifest.agent, "Explore")
        self.assertEqual(manifest.paths, ("**/*.py",))
        self.assertEqual(manifest.disallowed_tools, ("Write",))
        self.assertTrue(manifest.user_invocable)
        self.assertTrue(manifest.background)

    def test_bad_yaml_raises(self) -> None:
        path = _FIXTURES / "bad_yaml" / "SKILL.md"
        with self.assertRaises(SkillParseError) as ctx:
            parse_skill_md(path, profile=ParseProfile.PEON)
        self.assertEqual(ctx.exception.code, "yaml_syntax")

    def test_peon_profile_requires_dir_match(self) -> None:
        path = _FIXTURES / "claude-extensions" / "SKILL.md"
        parse_skill_manifest(path, profile=ParseProfile.PEON)
        nested = (
            _FIXTURES
            / "nested_root"
            / "project"
            / ".claude"
            / "skills"
            / "nested-demo"
            / "SKILL.md"
        )
        parse_skill_manifest(nested, profile=ParseProfile.PEON)
        # Dir name differs from command name → peon error; claude-code ok.
        mismatch = _FIXTURES / "list-tools" / "SKILL.md"
        with self.assertRaises(SkillParseError):
            text = mismatch.read_text(encoding="utf-8").replace(
                "name: list-tools",
                "name: other-name",
            )
            parse_skill_manifest(
                mismatch,
                profile=ParseProfile.PEON,
                text=text,
            )

    def test_nested_claude_skills_discovery(self) -> None:
        root = _FIXTURES / "nested_root" / "project"
        found = discover_skill_candidates(root, source="project")
        names = {c.root.name for c in found}
        self.assertIn("nested-demo", names)
        # Symlink-safe fingerprint is stable for the tree.
        stamp = skill_tree_fingerprint(root)
        self.assertTrue(len(stamp) >= 8)

    def test_linter_accepts_list_allowed_tools(self) -> None:
        skill_dir = _FIXTURES / "list-tools"
        result = SkillLinter.lint_dir(skill_dir)
        codes = {i["code"] for i in result["issues"]}
        self.assertNotIn("allowed_tools_type", codes)

    def test_linter_claude_fields_not_unexpected_under_peon(self) -> None:
        skill_dir = _FIXTURES / "claude-extensions"
        result = SkillLinter.lint_dir(skill_dir)
        unexpected = [
            i
            for i in result["issues"]
            if i["code"] == "unexpected_field"
            and "when_to_use" in i["message"]
        ]
        self.assertEqual(unexpected, [])
