import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
ROOT_CONFIG_FILE = ROOT_DIR / "config.json"


class ConfigLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._created_root_config = False
        if not ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            cls._created_root_config = True

        from services import config as config_module

        cls.config_module = config_module

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._created_root_config and ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.unlink()

    def test_load_settings_falls_back_to_example_when_config_path_is_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            data_dir = base_dir / "data"
            config_dir = base_dir / "config.json"
            example_file = base_dir / "config.example.json"

            config_dir.mkdir()
            example_file.write_text(
                json.dumps({"auth-key": "example-auth", "refresh_account_interval_minute": 15}),
                encoding="utf-8",
            )

            module = self.config_module
            old_base_dir = module.BASE_DIR
            old_data_dir = module.DATA_DIR
            old_config_file = module.CONFIG_FILE
            old_config_example_file = module.CONFIG_EXAMPLE_FILE
            try:
                module.BASE_DIR = base_dir
                module.DATA_DIR = data_dir
                module.CONFIG_FILE = config_dir
                module.CONFIG_EXAMPLE_FILE = example_file

                settings = module._load_settings()

                self.assertEqual(settings.auth_key, "example-auth")
                self.assertEqual(settings.refresh_account_interval_minute, 15)
            finally:
                module.BASE_DIR = old_base_dir
                module.DATA_DIR = old_data_dir
                module.CONFIG_FILE = old_config_file
                module.CONFIG_EXAMPLE_FILE = old_config_example_file


class _DummyThread:
    def join(self, timeout: float | None = None) -> None:
        return None


class _FakeChatGPTService:
    def __init__(self, _account_service) -> None:
        pass

    def generate_with_pool(self, prompt: str, model: str, n: int) -> dict[str, object]:
        return {
            "created": 1,
            "data": [{"b64_json": f"{prompt}:{model}:{n}"}],
        }

    def edit_with_pool(
        self,
        prompt: str,
        image_data: bytes,
        file_name: str,
        mime_type: str,
        model: str,
        n: int,
    ) -> dict[str, object]:
        return {
            "created": 1,
            "data": [{"b64_json": f"{prompt}:{file_name}:{mime_type}:{len(image_data)}:{model}:{n}"}],
        }


class PublicImageApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._created_root_config = False
        if not ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            cls._created_root_config = True

        from services import api as api_module

        cls.api_module = api_module

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._created_root_config and ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.unlink()

    @contextmanager
    def create_client(self):
        sample_accounts = [
            {"access_token": "token-a", "status": "可用", "quota": 4},
            {"access_token": "token-b", "status": "可用", "quota": 3},
            {"access_token": "token-c", "status": "可用", "quota": 0},
        ]

        with (
            mock.patch.object(self.api_module, "ChatGPTService", _FakeChatGPTService),
            mock.patch.object(
                self.api_module,
                "start_limited_account_watcher",
                return_value=_DummyThread(),
            ),
            mock.patch.object(
                self.api_module.account_service,
                "list_accounts",
                return_value=sample_accounts,
            ),
            TestClient(self.api_module.create_app()) as client,
        ):
            yield client

    def test_public_image_status_available_without_auth(self) -> None:
        with self.create_client() as client:
            response = client.get("/api/image/public-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "availableQuota": 7,
                "availableAccountCount": 2,
            },
        )

    def test_image_generation_available_without_auth(self) -> None:
        with self.create_client() as client:
            response = client.post(
                "/v1/images/generations",
                json={"prompt": "cat", "model": "gpt-image-1", "n": 1},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["b64_json"], "cat:gpt-image-1:1")

    def test_image_edit_available_without_auth(self) -> None:
        with self.create_client() as client:
            response = client.post(
                "/v1/images/edits",
                data={"prompt": "edit cat", "model": "gpt-image-1", "n": "1"},
                files={"image": ("cat.png", b"fake-image", "image/png")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"][0]["b64_json"],
            "edit cat:cat.png:image/png:10:gpt-image-1:1",
        )

    def test_accounts_endpoint_still_requires_auth(self) -> None:
        with self.create_client() as client:
            response = client.get("/api/accounts")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
