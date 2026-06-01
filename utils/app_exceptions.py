from typing import Any, Optional


class AppException(Exception):
    def __init__(
        self,
        http_status: int,
        biz_code: int,
        message: str,
        detail: Optional[Any] = None,
    ):
        super().__init__(message)
        self.http_status = http_status
        self.biz_code = biz_code
        self.message = message
        self.detail = detail

