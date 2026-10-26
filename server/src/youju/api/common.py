"""Small JSON API validation helpers; external inputs never select host paths."""

from flask import request


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def body(required=(), optional=()):
    if request.content_length is not None and request.content_length > 64 * 1024:
        raise ApiError(413, "INPUT_TOO_LARGE", "请求数据过大。")
    if len(request.get_data(cache=True)) > 64 * 1024:
        raise ApiError(413, "INPUT_TOO_LARGE", "请求数据过大。")
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or set(required) - value.keys():
        raise ApiError(400, "INVALID_INPUT", "请填写完整的请求数据。")
    if value.keys() - set(required) - set(optional):
        raise ApiError(400, "INVALID_INPUT", "请求包含不支持的字段。")
    return value


def text(value, key, limit=256, *, optional=False, trim=True):
    item = value.get(key, "" if optional else None)
    if not isinstance(item, str):
        raise ApiError(400, "INVALID_INPUT", f"{key} 必须为文本。")
    item = item.strip() if trim else item
    if len(item) > limit or (not optional and not item):
        raise ApiError(400, "INVALID_INPUT", f"请检查 {key} 的长度和内容。")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in item):
        raise ApiError(400, "INVALID_INPUT", f"{key} 包含不支持的字符。")
    return item
