from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from novel.core.migration import CURRENT_SCHEMA_VERSION
from novel.core.workspace import InitOptions, WorkspaceExistsError, init_workspace


class WorkspaceInitTest(unittest.TestCase):
    def test_init_creates_expected_workspace_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "rain-station"

            result = init_workspace(InitOptions(title="雨夜旧车站", root=root))

            self.assertEqual(result.root, root)
            self.assertTrue((root / "project.yaml").is_file())
            self.assertTrue((root / "config" / "agents.yaml").is_file())
            self.assertTrue((root / "config" / "embeddings.yaml").is_file())
            self.assertTrue((root / "memory" / "inspiration.md").is_file())
            self.assertTrue((root / "memory" / "style_guide.md").is_file())
            self.assertTrue((root / "memory" / "chapters").is_dir())
            self.assertTrue((root / "runs").is_dir())
            self.assertTrue((root / "exports").is_dir())

            expected_json_files = {
                root / "memory" / "canon" / "characters.json": "characters",
                root / "memory" / "canon" / "locations.json": "locations",
                root / "memory" / "canon" / "items.json": "items",
                root / "memory" / "canon" / "world.json": "world_rules",
                root / "memory" / "canon" / "hidden_truths.json": "hidden_truths",
                root / "memory" / "canon" / "foreshadowing.json": "foreshadowing_threads",
                root / "memory" / "state" / "timeline.json": "events",
            }

            for path, key in expected_json_files.items():
                with self.subTest(path=path):
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(data["schema_version"], CURRENT_SCHEMA_VERSION)
                    self.assertEqual(data[key], [])

            state = json.loads(
                (root / "memory" / "state" / "current_state.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(state["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertEqual(
                state["story_position"],
                {
                    "latest_chapter": 0,
                    "in_story_time": None,
                    "summary": None,
                },
            )
            self.assertEqual(state["character_states"], [])
            self.assertEqual(state["item_states"], [])
            self.assertEqual(state["location_states"], [])

    def test_init_writes_env_var_names_not_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"

            init_workspace(InitOptions(title="Test Novel", root=root))

            agents_yaml = (root / "config" / "agents.yaml").read_text(encoding="utf-8")
            embeddings_yaml = (root / "config" / "embeddings.yaml").read_text(encoding="utf-8")
            self.assertIn('api_key_env: "OPENAI_API_KEY"', agents_yaml)
            self.assertNotIn("sk-", agents_yaml)
            self.assertIn('api_key_env: "DASHSCOPE_API_KEY"', embeddings_yaml)
            self.assertNotIn("sk-", embeddings_yaml)

    def test_init_refuses_to_overwrite_existing_workspace_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"

            init_workspace(InitOptions(title="Test Novel", root=root))

            with self.assertRaises(WorkspaceExistsError):
                init_workspace(InitOptions(title="Test Novel", root=root))

    def test_cli_init_returns_error_when_workspace_exists(self) -> None:
        from novel.cli import main

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "workspace"

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                first = main(["init", "Test Novel", "--path", str(root)])
                second = main(["init", "Test Novel", "--path", str(root)])

            self.assertEqual(first, 0)
            self.assertEqual(second, 1)


if __name__ == "__main__":
    unittest.main()
