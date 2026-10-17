"""Pin BOLT11 route configuration to one path; preserve upstream router/scorer/retry."""
from pathlib import Path
import sys


def single_path(source):
    old = "                RouteParametersConfig::default(),\n                Retry::Timeout(Duration::from_secs(10)),"
    if source.count(old) != 1:
        raise ValueError("Pinned BOLT11 payment call changed; single-path enforcement cannot be verified")
    return source.replace(old, "                RouteParametersConfig::default().with_max_path_count(1),\n                Retry::Timeout(Duration::from_secs(10)),")


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_text(single_path(path.read_text()))
