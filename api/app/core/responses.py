from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class BaseAPIResponse(BaseModel, Generic[T]):
    success: bool = True
    response: T | None = None
    status_code: int = 200
    error_message: str | None = None
    error_code: int | None = None

    @classmethod
    def success_response(
        cls, response: T, status_code: int = 200
    ) -> BaseAPIResponse[T]:
        return cls(
            success=True,
            status_code=status_code,
            response=response,
            error_message=None,
            error_code=None,
        )

    @classmethod
    def error_response(
        cls,
        error_message: str,
        status_code: int = 400,
        error_code: int | None = None,
        response: T | None = None,
    ) -> BaseAPIResponse[T]:
        return cls(
            success=False,
            status_code=status_code,
            response=response,
            error_message=error_message,
            error_code=error_code,
        )
