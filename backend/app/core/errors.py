"""Application error model: stable error codes + unified HTTP error body."""

from __future__ import annotations


class AppError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "服务器内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        request_id: str | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.request_id = request_id
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code

    def to_body(self) -> dict:
        err: dict = {"code": self.code, "message": self.message}
        if self.request_id:
            err["request_id"] = self.request_id
        return {"error": err}


class InvalidRequest(AppError):
    status_code, code, default_message = 400, "INVALID_REQUEST", "请求参数非法"


class SessionNotFound(AppError):
    status_code, code, default_message = 404, "SESSION_NOT_FOUND", "会话不存在"


class RunNotFound(AppError):
    status_code, code, default_message = 404, "RUN_NOT_FOUND", "任务不存在"


class GenerationInProgress(AppError):
    status_code, code, default_message = 409, "GENERATION_IN_PROGRESS", "当前会话已有生成任务，请等待其结束。"


class SessionBusy(AppError):
    status_code, code, default_message = 409, "SESSION_BUSY", "会话正在生成中，暂不能删除。"


class IdempotencyKeyReused(AppError):
    status_code, code, default_message = (
        409,
        "IDEMPOTENCY_KEY_REUSED",
        "相同 request_id 对应了不同的请求内容。",
    )


class DependencyUnavailable(AppError):
    status_code, code, default_message = 503, "DEPENDENCY_UNAVAILABLE", "依赖服务不可用，暂时无法接受请求。"


class UpstreamError(AppError):
    status_code, code, default_message = 502, "UPSTREAM_ERROR", "模型服务调用失败"


class ContextTooLong(AppError):
    status_code, code, default_message = 400, "CONTEXT_TOO_LONG", "当前输入超出模型上下文预算"
