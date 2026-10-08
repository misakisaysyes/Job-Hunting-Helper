"""The CLI must honor config defaults and override only explicit arguments."""

from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_CONFIG
from cli import cli as cli_entry
from collector.platforms.boss import build_boss_request
from run import validate_run_config


class CliConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = cli_entry.build_parser()
        self.defaults = deepcopy(DEFAULT_CONFIG)
        self.defaults["collection"].update(
            mode="search", encrypt_expect_id=[], keywords=["前端"], max_pages=2)
        self.defaults["profile"].update(
            education=["本科"], company_sizes=["500-999人"],
            recruitment_types=["experienced"], filter_unparsed_salary=True,
            exclude_headhunter=True, deal_breakers=["外包"],
        )

    def resolve(self, *arguments: str) -> dict:
        with patch.object(cli_entry, "DEFAULT_CONFIG", self.defaults):
            return cli_entry.resolve_config(self.parser.parse_args(arguments), self.parser)

    def test_every_config_leaf_has_a_cli_option(self) -> None:
        configured = {(section, key) for section, values in DEFAULT_CONFIG.items()
                      for key in values}
        self.assertEqual(configured, set(cli_entry.CONFIG_OPTIONS.values()))
        parsed = {action.dest for action in self.parser._actions}
        self.assertTrue(set(cli_entry.CONFIG_OPTIONS).issubset(parsed))

    def test_unset_cli_arguments_preserve_config_defaults(self) -> None:
        self.assertEqual(self.resolve(), self.defaults)
        self.assertEqual(self.resolve()["collection"]["platform"], "boss")

    def test_platform_can_be_set_from_cli_and_rejects_unavailable_config(self) -> None:
        self.assertEqual(self.resolve("--platform", "boss")["collection"]["platform"], "boss")
        self.defaults["collection"]["platform"] = "other"
        with self.assertRaisesRegex(ValueError, "当前不支持采集平台"):
            validate_run_config(deepcopy(self.defaults))

    def test_explicit_cli_values_replace_lists_and_disable_flags(self) -> None:
        resolved = self.resolve(
            "--keyword", "后端", "--education", "大专,硕士",
            "--company-size", "20-99人", "--recruitment-type", "internship",
            "--deal-breaker", "驻场,夜班", "--no-filter-unparsed-salary",
            "--no-exclude-headhunter", "--max-pages", "3", "--ai-model", "example-model",
            "--ai-api-concurrency", "3",
            "--use-ai-score", "--ai-score-user-prompt", "重点看工程经验",
            "--ai-retry-count", "2",
            "--ai-retry-delay-min-seconds", "0.5",
            "--ai-retry-delay-max-seconds", "2.5",
        )
        self.assertEqual(resolved["collection"]["keywords"], ["后端"])
        self.assertEqual(resolved["collection"]["max_pages"], 3)
        self.assertEqual(resolved["profile"]["education"], ["大专", "硕士"])
        self.assertEqual(resolved["profile"]["company_sizes"], ["20-99人"])
        self.assertEqual(resolved["profile"]["recruitment_types"], ["internship"])
        self.assertEqual(resolved["profile"]["deal_breakers"], ["驻场", "夜班"])
        self.assertFalse(resolved["profile"]["filter_unparsed_salary"])
        self.assertFalse(resolved["profile"]["exclude_headhunter"])
        self.assertEqual(resolved["ai"]["model"], "example-model")
        self.assertEqual(resolved["ai"]["ai_api_concurrency"], 3)
        self.assertTrue(resolved["ai"]["use_ai_score"])
        self.assertEqual(resolved["ai"]["score_user_prompt"], "重点看工程经验")
        self.assertEqual(resolved["ai"]["retry_count"], 2)
        self.assertEqual(resolved["ai"]["retry_delay_min_seconds"], 0.5)
        self.assertEqual(resolved["ai"]["retry_delay_max_seconds"], 2.5)
        self.assertEqual(self.defaults["profile"]["education"], ["本科"])

    def test_ai_api_concurrency_must_be_positive(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            self.resolve("--ai-api-concurrency", "0")

    def test_ai_retry_wait_range_must_be_valid(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            self.resolve("--ai-retry-delay-min-seconds", "3",
                         "--ai-retry-delay-max-seconds", "1")

    def test_ai_retry_count_must_be_nonnegative(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            self.resolve("--ai-retry-count", "-1")

    def test_ai_score_flag_can_override_config_in_both_directions(self) -> None:
        self.assertEqual(self.resolve()["ai"]["use_ai_score"], self.defaults["ai"]["use_ai_score"])
        self.assertTrue(self.resolve("--use-ai-score")["ai"]["use_ai_score"])
        self.defaults["ai"]["use_ai_score"] = True
        self.assertTrue(self.resolve()["ai"]["use_ai_score"])
        self.assertFalse(self.resolve("--no-use-ai-score")["ai"]["use_ai_score"])

    def test_clear_list_override(self) -> None:
        resolved = self.resolve("--clear-education", "--clear-deal-breaker")
        self.assertEqual(resolved["profile"]["education"], [])
        self.assertEqual(resolved["profile"]["deal_breakers"], [])

    def test_mode_can_come_from_config_and_be_overridden(self) -> None:
        self.assertEqual(self.resolve()["collection"]["mode"], "search")
        resolved = self.resolve("--mode", "recommend", "--clear-keyword",
                                "--encrypt-expect-id", "first-id,second-id",
                                "--encrypt-expect-id", "second-id")
        self.assertEqual(resolved["collection"]["mode"], "recommend")
        self.assertEqual(resolved["collection"]["keywords"], [])
        self.assertEqual(resolved["collection"]["encrypt_expect_id"], ["first-id", "second-id"])

    def test_configured_expect_ids_are_used_when_cli_omits_them(self) -> None:
        self.defaults["collection"].update(
            mode="recommend", keywords=[], encrypt_expect_id=["first-id", "second-id"],
        )
        self.assertEqual(
            self.resolve()["collection"]["encrypt_expect_id"], ["first-id", "second-id"],
        )
        resolved = self.resolve("--encrypt-expect-id", "third-id,fourth-id")
        self.assertEqual(resolved["collection"]["encrypt_expect_id"], ["third-id", "fourth-id"])

    def test_boss_request_uses_config_mappings_and_custom_city_code(self) -> None:
        config = self.resolve("--city", "自定义城市", "--city-code", "123456789",
                              "--experience", "1年内,3-5")
        request = build_boss_request(config)
        self.assertEqual(request.city_codes, {"自定义城市": "123456789"})
        self.assertEqual(request.filters["experience"], ["1年以内", "3-5年"])
        self.assertEqual(request.filters["degree"], ["本科"])
        self.assertEqual(request.filters["scale"], ["500-999人"])


if __name__ == "__main__":
    unittest.main()
