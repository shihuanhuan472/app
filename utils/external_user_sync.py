import asyncio
import json
import logging
import os
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_retry_delays() -> List[float]:
    raw = os.getenv(
        "EXTERNAL_USER_SYNC_RETRY_DELAYS",
        "5,30,300",
    )

    delays = [0.0]

    try:
        for item in raw.split(","):
            item = item.strip()
            if item:
                delays.append(max(float(item), 0.0))
    except ValueError:
        delays = [0.0, 5.0, 30.0, 300.0]

    if len(delays) == 1:
        delays.extend([5.0, 30.0, 300.0])

    return delays


async def _post_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _env_bool("EXTERNAL_USER_SYNC_ENABLED", False):
        return {
            "status": "disabled",
            "message": "外部用户同步未启用",
        }

    url = os.getenv("EXTERNAL_USER_SYNC_URL", "").strip()
    token = os.getenv("EXTERNAL_USER_SYNC_TOKEN", "").strip()

    if not url or not token:
        logger.error("外部用户同步配置缺失")
        return {
            "status": "config_error",
            "message": "外部用户同步地址或令牌未配置",
        }

    try:
        timeout = float(os.getenv("EXTERNAL_USER_SYNC_TIMEOUT", "10"))
    except ValueError:
        timeout = 10.0

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Sync-Token": token,
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    retry_delays = _get_retry_delays()
    log_name = payload.get("username", "batch")

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for attempt, delay in enumerate(retry_delays):
            if delay > 0:
                await asyncio.sleep(delay)

            try:
                response = await client.post(url, content=body)
            except httpx.HTTPError as exc:
                if attempt < len(retry_delays) - 1:
                    logger.warning(
                        "外部用户同步网络异常，将重试 username=%s attempt=%s",
                        log_name,
                        attempt + 1,
                    )
                    continue

                logger.error(
                    "外部用户同步失败，已达到最大重试次数 username=%s error_type=%s",
                    log_name,
                    type(exc).__name__,
                )
                return {
                    "status": "retry_exhausted",
                    "message": "外部接口网络请求失败",
                }

            try:
                response_body = response.json()
            except ValueError:
                response_body = {}

            external_code = None
            external_message = None

            if isinstance(response_body, dict):
                external_code = response_body.get("code")
                external_message = (
                    response_body.get("message")
                    or response_body.get("msg")
                )

            if response.status_code == 200 and str(external_code) == "0":
                return {
                    "status": "success",
                    "http_status": response.status_code,
                    "data": response_body.get("data"),
                }

            if response.status_code == 409 and str(external_code) == "40902":
                return {
                    "status": "exists",
                    "http_status": response.status_code,
                    "message": external_message or "用户已存在",
                }

            if response.status_code in {500, 502, 503, 504}:
                if attempt < len(retry_delays) - 1:
                    logger.warning(
                        "外部用户同步返回临时错误，将重试 username=%s "
                        "http_status=%s attempt=%s",
                        log_name,
                        response.status_code,
                        attempt + 1,
                    )
                    continue

                return {
                    "status": "retry_exhausted",
                    "http_status": response.status_code,
                    "message": external_message or "外部接口暂时不可用",
                }

            logger.error(
                "外部用户同步被拒绝 username=%s http_status=%s external_code=%s",
                log_name,
                response.status_code,
                external_code,
            )
            return {
                "status": "failed",
                "http_status": response.status_code,
                "external_code": external_code,
                "message": external_message or "外部用户同步失败",
            }

    return {
        "status": "failed",
        "message": "外部用户同步失败",
    }


async def sync_external_user(
    username: str,
    password: str,
    real_name: str,
    phone: str,
    update_password: bool = False,
) -> Dict[str, Any]:
    return await _post_payload(
        {
            "username": username,
            "password": password,
            "real_name": real_name,
            "phone": phone,
            "update_password": update_password,
        }
    )


async def sync_external_users(
    users: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not users:
        raise ValueError("批量同步用户不能为空")

    if len(users) > 500:
        raise ValueError("批量同步一次最多 500 条用户")

    return await _post_payload({"users": users})
