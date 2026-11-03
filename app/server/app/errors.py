from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException


class Problem(Exception):
    def __init__(self, code, message, status=422, details=None, retryable=False):
        self.code, self.message, self.status = code, message, status
        self.details, self.retryable = details or {}, retryable


def register_errors(app):
    @app.exception_handler(Problem)
    async def problem(request: Request, error: Problem):
        return JSONResponse(status_code=error.status, content={"code": error.code, "message": error.message,
            "requestId": request.state.request_id, "retryable": error.retryable, "details": error.details})

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error):
        return await problem(request, Problem("INVALID_REQUEST", "请求字段不符合要求", details={"fields": [".".join(map(str, e["loc"])) for e in error.errors()]}))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error):
        return await problem(request, Problem("RESOURCE_NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR", "请求不可用", error.status_code))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error):
        import logging
        logging.getLogger("reader").error("request=%s error=%s", request.state.request_id, type(error).__name__)
        return await problem(request, Problem("PERSISTENCE_UNAVAILABLE", "服务暂时不可用，请保留输入后重试", 503, retryable=True))
