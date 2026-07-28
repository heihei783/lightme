import os
import tempfile
import textwrap
import unittest

from app.agent.skill_loader import parse_skill_md, scan_skill_files


class SkillLoaderCompatTest(unittest.TestCase):
    def test_parse_lightme_skill_format_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "demo.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(textwrap.dedent("""
                    # Skill: demo_skill

                    ## Description
                    Demo skill.

                    ## Category
                    execute

                    ## Trigger
                    - demo

                    ## Instructions
                    Use the demo flow.
                """).strip())

            skill = parse_skill_md(path)

            self.assertIsNotNone(skill)
            self.assertEqual(skill.name, "demo_skill")
            self.assertEqual(skill.source_format, "lightme")
            self.assertEqual(skill.category, "execute")
            self.assertIn("demo", skill.keywords)

    def test_parse_claude_skill_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "pdf")
            os.makedirs(skill_dir)
            path = os.path.join(skill_dir, "SKILL.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(textwrap.dedent("""
                    ---
                    name: pdf
                    description: Use this skill whenever the user mentions PDF files.
                    license: Proprietary
                    ---

                    # PDF Processing Guide

                    Read, merge, split, and fill PDFs.
                """).strip())

            skill = parse_skill_md(path)

            self.assertIsNotNone(skill)
            self.assertEqual(skill.name, "pdf")
            self.assertEqual(skill.source_format, "claude")
            self.assertIn("PDF Processing Guide", skill.instructions)
            self.assertIn("pdf", [kw.lower() for kw in skill.keywords])

    def test_scan_skill_files_finds_nested_claude_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "browser")
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(textwrap.dedent("""
                    ---
                    name: browser-control
                    description: Control browser pages.
                    ---

                    # Browser Control
                """).strip())

            skills = scan_skill_files(tmp)

            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0].name, "browser-control")
            self.assertEqual(skills[0].source_format, "claude")


if __name__ == "__main__":
    unittest.main()
