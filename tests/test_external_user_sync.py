import json

import httpx
import pytest

from utils import external_user_sync


class FakeAsyncClient:
    response_specs = []
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.posts = []
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, content):
        self.posts.append({"url": url, "content": content})
        status_code, payload = self.__class__.response_specs.pop(0)
        return httpx.Response(status_code, json=payload)


@pytest.fixture(autouse=True)
def reset_fake_client():
    FakeAsyncClient.response_specs = []
    FakeAsyncClient.instances = []


def configure_sync(monkeypatch):
    monkeypatch.setenv("EXTERNAL_USER_SYNC_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_USER_SYNC_URL", "http://example.test/users/sync")
    monkeypatch.setenv("EXTERNAL_USER_SYNC_TOKEN", "test-token")
    monkeypatch.setenv("EXTERNAL_USER_SYNC_TIMEOUT", "10")
    monkeypatch.setenv("EXTERNAL_USER_SYNC_RETRY_DELAYS", "0,0,0")
    monkeypatch.setattr(external_user_sync.httpx, "AsyncClient", FakeAsyncClient)


@pytest.mark.asyncio
async def test_sync_disabled_does_not_send_request(monkeypatch):
    monkeypatch.setenv("EXTERNAL_USER_SYNC_ENABLED", "false")

    result = await external_user_sync.sync_external_user(
        username="zhangsan",
        password="secret",
        real_name="张三",
        phone="13800000000",
    )

    assert result["status"] == "disabled"
    assert FakeAsyncClient.instances == []


@pytest.mark.asyncio
async def test_single_sync_sends_utf8_json_and_token(monkeypatch):
    configure_sync(monkeypatch)
    FakeAsyncClient.response_specs = [
        (200, {"code": 0, "message": "同步成功", "data": {"id": 123}}),
    ]

    result = await external_user_sync.sync_external_user(
        username="zhangsan",
        password="secret",
        real_name="张三",
        phone="13800000000",
    )

    client = FakeAsyncClient.instances[0]
    request_body = json.loads(client.posts[0]["content"].decode("utf-8"))

    assert result == {
        "status": "success",
        "http_status": 200,
        "data": {"id": 123},
    }
    assert client.kwargs["headers"]["X-Sync-Token"] == "test-token"
    assert client.kwargs["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert request_body == {
        "username": "zhangsan",
        "password": "secret",
        "real_name": "张三",
        "phone": "13800000000",
        "update_password": False,
    }


@pytest.mark.asyncio
async def test_existing_user_is_treated_as_idempotent_success(monkeypatch):
    configure_sync(monkeypatch)
    FakeAsyncClient.response_specs = [
        (409, {"code": 40902, "message": "用户名已存在"}),
    ]

    result = await external_user_sync.sync_external_user(
        username="zhangsan",
        password="secret",
        real_name="张三",
        phone="13800000000",
    )

    assert result["status"] == "exists"
    assert len(FakeAsyncClient.instances[0].posts) == 1


@pytest.mark.asyncio
async def test_temporary_server_error_is_retried(monkeypatch):
    configure_sync(monkeypatch)
    FakeAsyncClient.response_specs = [
        (502, {"message": "bad gateway"}),
        (200, {"code": 0, "message": "同步成功", "data": {"id": 123}}),
    ]

    result = await external_user_sync.sync_external_user(
        username="zhangsan",
        password="secret",
        real_name="张三",
        phone="13800000000",
        update_password=True,
    )

    assert result["status"] == "success"
    assert len(FakeAsyncClient.instances[0].posts) == 2


@pytest.mark.asyncio
async def test_batch_sync_rejects_more_than_500_users():
    with pytest.raises(ValueError, match="500"):
        await external_user_sync.sync_external_users([{}] * 501)
