from __future__ import annotations

from novel.core.command_registry import command_catalog

from .common import WebCommandRequest, WebResponsePayload
from .deps import __version__


def openapi_document() -> dict[str, object]:
    """返回本地 Web canonical command endpoint 的可生成契约。"""

    request_schema = WebCommandRequest.model_json_schema(ref_template="#/components/schemas/{model}")
    response_schema = WebResponsePayload.model_json_schema(ref_template="#/components/schemas/{model}")
    components: dict[str, object] = {}
    components.update(request_schema.pop("$defs", {}))
    components.update(response_schema.pop("$defs", {}))
    components["WebCommandRequest"] = request_schema
    components["WebResponsePayload"] = response_schema
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "WriterYang 本地 Web API",
            "version": __version__,
            "description": "CLI 与 Web 共享 Command Bus 的 canonical typed command endpoint。",
        },
        "servers": [{"url": "http://127.0.0.1"}],
        "paths": {
            "/api/command": {
                "post": {
                    "operationId": "dispatchCommand",
                    "summary": "执行一个结构化公开 command",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/WebCommandRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Command 执行成功",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/WebResponsePayload"}
                                }
                            },
                        },
                        "4XX": {"description": "结构化输入或领域错误"},
                        "500": {"description": "服务内部错误；使用 request_id 查询本地日志"},
                    },
                }
            }
        },
        "components": {"schemas": components},
        "x-writeryang-command-catalog": command_catalog(),
    }
