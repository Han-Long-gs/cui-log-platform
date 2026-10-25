from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .router import router

app = FastAPI()


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, ex: RequestValidationError) -> JSONResponse:
    """Return 422 with one {"loc", "msg"} entry per request validation error."""
    message = {"error": "Validation Error", "detail": []}
    errors = ex.errors()
    for error in errors:
        message["detail"].append({"loc": error["loc"], "msg": error["msg"]})
    return JSONResponse(message, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)


app.include_router(router=router)
