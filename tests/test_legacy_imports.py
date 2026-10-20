import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_existing_scripts_remain_importable():
    for filename in ("main.py", "lesson02.py", "collector.py", "my_activity.py"):
        spec = importlib.util.spec_from_file_location(f"legacy_{filename[:-3]}", ROOT / filename)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
