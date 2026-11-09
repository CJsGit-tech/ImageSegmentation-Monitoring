"""Which events the system log keeps (PRD-14-R6), the groups Admin filters on, and the short
Chinese description of each row (PRD-14-R7).

Only events listed here are written. Everything else (account registration, startup notices,
anything about generated readings) stays on stdout only.
"""

from collections.abc import Callable
from typing import Any

from app.auth.permissions import CAPABILITY_LABELS, Capability
from app.models.system_log import SUMMARY_MAX_LENGTH, Severity

SYSTEM_ERROR = "system_error"

# Group key -> label, in the order the screen offers them.
EVENT_TYPE_LABELS: dict[str, str] = {
    "login_success": "登入成功",
    "login_failure": "登入失敗",
    "logout": "登出",
    "login_superseded": "被別處登入",
    "permission_denied": "權限被拒",
    "role_changed": "角色變更",
    "record_created": "資料新增",
    "record_updated": "資料修改",
    "record_deleted": "資料刪除",
    "import_result": "批量導入結果",
    "range_unit_changed": "異常範圍／單位變更",
    SYSTEM_ERROR: "系統錯誤",
}

SEVERITY_LABELS: dict[Severity, str] = {Severity.INFO: "資訊", Severity.WARNING: "警告", Severity.ERROR: "錯誤"}

_RANGE_UNIT_EVENTS = tuple(
    f"category_{what}_{verb}" for what in ("range", "unit") for verb in ("set", "changed", "cleared")
)

# Audit event name (app.auth.events.audit) -> group key.
EVENT_GROUPS: dict[str, str] = {
    "login_success": "login_success",
    "login_failure": "login_failure",
    "logout": "logout",
    "login_superseded": "login_superseded",
    "permission_denied": "permission_denied",
    "user_role_changed": "role_changed",
    "record_created": "record_created",
    "record_updated": "record_updated",
    "record_deleted": "record_deleted",
    "records_imported": "import_result",
    "records_import_rejected": "import_result",
    **{name: "range_unit_changed" for name in _RANGE_UNIT_EVENTS},
    SYSTEM_ERROR: SYSTEM_ERROR,
}

_LOGIN_FAILURE_REASONS = {"unknown_account": "帳號不存在", "wrong_password": "密碼錯誤"}
# The reason codes of app.records.import_errors.
_IMPORT_REASONS = {
    "refused": "無法導入",
    "rows_invalid": "資料有錯誤",
    "filename_taken": "同名檔案已導入過",
    "unsupported_format": "不支援的格式",
    "unparseable": "無法解析檔案",
    "empty": "檔案沒有資料",
    "too_large": "檔案過大",
    "too_many_rows": "筆數超過上限",
    "filename_invalid": "檔名不符規則",
    "file_missing": "未附檔案",
}


def _record(fields: dict[str, Any]) -> str:
    record_id = fields.get("record_id")
    return f"記錄 #{record_id}" if record_id is not None else "記錄"


def _number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "—" if value is None else str(value)


def _range(low: Any, high: Any) -> str:
    return f"{_number(low)} ～ {_number(high)}"


def _unit(value: Any) -> str:
    return f"「{value}」" if value else "（無）"


def _permission_denied(fields: dict[str, Any]) -> str:
    reason = fields.get("reason")
    if reason == "not_record_owner":
        return f"只能修改或刪除自己建立的資料，{_record(fields)}被拒絕"
    # Anomalies are editable now, but rows logged while they were locked still carry this reason.
    if reason == "anomaly_read_only":
        return f"異常{_record(fields)}不可修改或刪除"
    if reason == "own_role_change":
        return "不能變更自己的角色"
    try:
        action = CAPABILITY_LABELS[Capability(fields.get("capability"))]
    except ValueError:
        action = "執行此操作"
    role = fields.get("role")
    return f"嘗試{action}被拒絕" + (f"（{role}）" if role else "")


def _import_rejected(fields: dict[str, Any]) -> str:
    name = fields.get("import_filename") or "檔案"
    reason = fields.get("reason")
    text = f"{name}：導入被拒絕（{_IMPORT_REASONS.get(reason, reason or '未知原因')}"
    if fields.get("error_count"):
        text += f"，{fields['error_count']} 處錯誤"
    return text + "）"


def _range_text(verb: str, fields: dict[str, Any]) -> str:
    category = fields.get("category")
    new = _range(fields.get("new_min"), fields.get("new_max"))
    old = _range(fields.get("old_min"), fields.get("old_max"))
    if verb == "set":
        return f"設定「{category}」的異常範圍：{new}"
    if verb == "changed":
        return f"修改「{category}」的異常範圍：{old} → {new}"
    return f"清除「{category}」的異常範圍（原為 {old}）"


def _unit_text(verb: str, fields: dict[str, Any]) -> str:
    category = fields.get("category")
    if verb == "set":
        return f"設定「{category}」的單位：{_unit(fields.get('new_unit'))}"
    if verb == "changed":
        return f"修改「{category}」的單位：{_unit(fields.get('old_unit'))} → {_unit(fields.get('new_unit'))}"
    return f"清除「{category}」的單位（原為 {_unit(fields.get('old_unit'))}）"


_SUMMARIES: dict[str, Callable[[dict[str, Any]], str]] = {
    "login_success": lambda f: "登入成功" + (f"（{f['role']}）" if f.get("role") else ""),
    "login_failure": lambda f: "登入失敗：" + _LOGIN_FAILURE_REASONS.get(f.get("reason"), "帳號或密碼錯誤"),
    "logout": lambda f: "登出",
    "login_superseded": lambda f: "在別處登入，先前的登入已失效",
    "permission_denied": _permission_denied,
    "user_role_changed": lambda f: (
        f"將 {f.get('target_username')} 的角色由 {f.get('old_role')} 改為 {f.get('new_role')}"
    ),
    "record_created": lambda f: f"新增{_record(f)}",
    "record_updated": lambda f: f"修改{_record(f)}" + (f"（欄位：{'、'.join(f['fields'])}）" if f.get("fields") else ""),
    "record_deleted": lambda f: f"刪除{_record(f)}",
    "records_imported": lambda f: (
        f"{f.get('import_filename')}：導入 {f.get('row_count')} 筆"
        + (f"（異常 {f['anomaly_count']} 筆）" if f.get("anomaly_count") else "")
    ),
    "records_import_rejected": _import_rejected,
    **{f"category_range_{verb}": (lambda f, verb=verb: _range_text(verb, f)) for verb in ("set", "changed", "cleared")},
    **{f"category_unit_{verb}": (lambda f, verb=verb: _unit_text(verb, f)) for verb in ("set", "changed", "cleared")},
    SYSTEM_ERROR: lambda f: "系統錯誤：" + str(f.get("message") or "未預期的錯誤"),
}


def summarize(event_name: str, fields: dict[str, Any]) -> str:
    try:
        text = _SUMMARIES[event_name](fields)
    except Exception:  # A description must never stop an event from being kept.
        text = EVENT_TYPE_LABELS[EVENT_GROUPS[event_name]]
    if len(text) > SUMMARY_MAX_LENGTH:
        text = text[: SUMMARY_MAX_LENGTH - 1] + "…"
    return text
