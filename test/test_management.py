import hashlib
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
ROOT_CONFIG_FILE = ROOT_DIR / "config.json"

AUTH_HEADER = {"Authorization": "Bearer test-auth"}


def _name_for(token: str) -> str:
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:16]
    return f"chatgpt-{digest}.json"


class _DummyThread:
    def join(self, timeout: float | None = None) -> None:
        return None


class _FakeChatGPTService:
    def __init__(self, _account_service) -> None:
        pass


class ManagementAuthFilesTests(unittest.TestCase):
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
        fake_accounts = {
            "token-a": {
                "access_token": "token-a",
                "email": "a@example.com",
                "user_id": "uid-a",
                "type": "Plus",
                "status": "正常",
                "quota": 5,
                "limits_progress": [],
                "default_model_slug": None,
                "restore_at": None,
                "success": 0,
                "fail": 0,
                "last_used_at": "2026-04-20 10:00:00",
            },
            "token-b": {
                "access_token": "token-b",
                "email": None,
                "user_id": None,
                "type": "Free",
                "status": "限流",
                "quota": 0,
                "limits_progress": [],
                "default_model_slug": None,
                "restore_at": None,
                "success": 0,
                "fail": 0,
                "last_used_at": None,
            },
            "token-c": {
                "access_token": "token-c",
                "email": "c@example.com",
                "user_id": "uid-c",
                "type": "Team",
                "status": "禁用",
                "quota": 0,
                "limits_progress": [],
                "default_model_slug": None,
                "restore_at": None,
                "success": 0,
                "fail": 0,
                "last_used_at": "2026-04-20 09:00:00",
            },
        }

        service = self.api_module.account_service

        with (
            mock.patch.object(self.api_module, "ChatGPTService", _FakeChatGPTService),
            mock.patch.object(
                self.api_module,
                "start_limited_account_watcher",
                return_value=_DummyThread(),
            ),
            mock.patch.object(service, "list_tokens", return_value=list(fake_accounts.keys())),
            mock.patch.object(service, "get_account", side_effect=lambda token: fake_accounts.get(token)),
            TestClient(self.api_module.create_app()) as client,
        ):
            yield client, fake_accounts

    # ── authentication -------------------------------------------------

    def test_list_without_authorization_returns_cpa_error(self) -> None:
        with self.create_client() as (client, _):
            response = client.get("/v0/management/auth-files")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "missing management key"})

    def test_list_with_invalid_authorization_returns_cpa_error(self) -> None:
        with self.create_client() as (client, _):
            response = client.get(
                "/v0/management/auth-files",
                headers={"Authorization": "Bearer wrong"},
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "invalid management key"})

    # ── list -----------------------------------------------------------

    def test_list_returns_cpa_compatible_entries(self) -> None:
        with self.create_client() as (client, accounts):
            response = client.get("/v0/management/auth-files", headers=AUTH_HEADER)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("files", body)
        files = body["files"]
        self.assertEqual(len(files), 3)

        by_name = {entry["name"]: entry for entry in files}
        expected_names = {_name_for(token) for token in accounts}
        self.assertEqual(set(by_name.keys()), expected_names)

        plus_entry = by_name[_name_for("token-a")]
        self.assertEqual(plus_entry["provider"], "chatgpt")
        self.assertEqual(plus_entry["account_type"], "chatgpt")
        self.assertEqual(plus_entry["source"], "file")
        self.assertFalse(plus_entry["runtime_only"])
        self.assertFalse(plus_entry["disabled"])
        self.assertFalse(plus_entry["unavailable"])
        self.assertEqual(plus_entry["status"], "ready")
        self.assertEqual(plus_entry["email"], "a@example.com")
        self.assertEqual(plus_entry["label"], "Plus")
        self.assertNotIn("access_token", plus_entry)

        throttled_entry = by_name[_name_for("token-b")]
        self.assertEqual(throttled_entry["status"], "throttled")
        self.assertTrue(throttled_entry["unavailable"])
        self.assertFalse(throttled_entry["disabled"])
        self.assertIsNone(throttled_entry["email"])

        disabled_entry = by_name[_name_for("token-c")]
        self.assertEqual(disabled_entry["status"], "disabled")
        self.assertTrue(disabled_entry["disabled"])
        self.assertTrue(disabled_entry["unavailable"])

    # ── download -------------------------------------------------------

    def test_download_without_authorization_returns_cpa_error(self) -> None:
        with self.create_client() as (client, _):
            response = client.get(
                f"/v0/management/auth-files/download?name={_name_for('token-a')}"
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "missing management key"})

    def test_download_returns_access_token_payload(self) -> None:
        target_name = _name_for("token-a")
        with self.create_client() as (client, _):
            response = client.get(
                f"/v0/management/auth-files/download?name={target_name}",
                headers=AUTH_HEADER,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-type", "").split(";")[0], "application/json")
        content_disposition = response.headers.get("content-disposition", "")
        self.assertIn("attachment", content_disposition)
        self.assertIn(target_name, content_disposition)

        payload = response.json()
        self.assertEqual(payload["access_token"], "token-a")
        self.assertEqual(payload["provider"], "chatgpt")
        self.assertEqual(payload["account_type"], "chatgpt")
        self.assertEqual(payload["email"], "a@example.com")
        self.assertEqual(payload["label"], "Plus")

    def test_download_with_empty_name_returns_invalid_body(self) -> None:
        with self.create_client() as (client, _):
            response = client.get("/v0/management/auth-files/download", headers=AUTH_HEADER)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid body"})

    def test_download_with_non_json_name_returns_invalid_body(self) -> None:
        with self.create_client() as (client, _):
            response = client.get(
                "/v0/management/auth-files/download?name=chatgpt-abc.txt",
                headers=AUTH_HEADER,
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid body"})

    def test_download_with_unknown_name_returns_file_not_found(self) -> None:
        with self.create_client() as (client, _):
            response = client.get(
                "/v0/management/auth-files/download?name=chatgpt-deadbeefdeadbeef.json",
                headers=AUTH_HEADER,
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "file not found"})


if __name__ == "__main__":
    unittest.main()
