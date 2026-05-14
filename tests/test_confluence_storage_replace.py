"""Unit tests for Confluence storage replace helpers (no live Atlassian calls)."""
import os
import unittest

# Minimal env so pydantic Settings() loads before importing project modules.
os.environ.setdefault("JIRA_URL", "https://jira.invalid.example")
os.environ.setdefault("JIRA_USER", "svc")
os.environ.setdefault("JIRA_PAT", "pat-invalid")
os.environ.setdefault("CONFLUENCE_URL", "https://confluence.invalid.example")
os.environ.setdefault("CONFLUENCE_PAT", "pat-invalid")
os.environ.setdefault("MCP_API_KEY", "test-key-local")

from atlassian_mcp.tools.common import ToolError
from atlassian_mcp.tools.confluence_storage_replace import (
    _apply_literal_replacement,
    _apply_regex_replacement,
    _normalize_replacement_rules,
)


class LiteralReplacementTests(unittest.TestCase):
    def test_replaces_all_by_default(self) -> None:
        text = "x__y__z"
        new_text, applied, eligible, warnings = _apply_literal_replacement(
            text, "__", "-", None, 10_000
        )
        self.assertEqual(new_text, "x-y-z")
        self.assertEqual(applied, 2)
        self.assertEqual(eligible, 2)
        self.assertEqual(warnings, [])

    def test_respects_max_occurrences(self) -> None:
        text = "aaa"
        new_text, applied, eligible, warnings = _apply_literal_replacement(
            text, "a", "b", 1, 10_000
        )
        self.assertEqual(new_text, "baa")
        self.assertEqual(applied, 1)
        self.assertEqual(eligible, 3)
        self.assertTrue(warnings)

    def test_no_match(self) -> None:
        new_text, applied, eligible, warnings = _apply_literal_replacement(
            "hello", "ZZZ", "!", None, 10_000
        )
        self.assertEqual(new_text, "hello")
        self.assertEqual(applied, 0)
        self.assertEqual(eligible, 0)
        self.assertEqual(warnings, [])


class RegexReplacementTests(unittest.TestCase):
    def test_subn_respects_count(self) -> None:
        text = "foo foo foo"
        new_text, applied, eligible, warnings = _apply_regex_replacement(
            text,
            r"foo",
            "bar",
            2,
            500,
            2.0,
        )
        self.assertEqual(new_text, "bar bar foo")
        self.assertEqual(applied, 2)
        self.assertGreaterEqual(eligible, 2)


class NormalizeRulesTests(unittest.TestCase):
    def test_rejects_empty_find(self) -> None:
        with self.assertRaises(ToolError):
            _normalize_replacement_rules([{"find": "", "replace": "x", "match": "literal"}])


if __name__ == "__main__":
    unittest.main()
