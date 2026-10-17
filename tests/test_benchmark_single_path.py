"""Offline contract checks for the benchmark-only BOLT11 path limit."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("benchmark_single_path", Path(__file__).resolve().parents[1] / "docker/single_path.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SinglePathContract(unittest.TestCase):
    def test_preserves_upstream_retry_and_surrounding_code(self):
        source = "prefix\n                RouteParametersConfig::default(),\n                Retry::Timeout(Duration::from_secs(10)),\nsuffix"
        result = module.single_path(source)
        self.assertEqual(result, source.replace("RouteParametersConfig::default()", "RouteParametersConfig::default().with_max_path_count(1)"))

    def test_drift_or_duplicate_anchor_blocks_build(self):
        anchor = "                RouteParametersConfig::default(),\n                Retry::Timeout(Duration::from_secs(10)),"
        for source in ("changed source", anchor + anchor, module.single_path(anchor)):
            with self.subTest(source=source), self.assertRaises(ValueError):
                module.single_path(source)
