"""The role matrix (PRD-02-R6). This is its only definition; everything else asks here."""

import enum

from app.models.user import Role, User


class Capability(enum.StrEnum):
    MONITORING_VIEW = "monitoring.view"
    RECORDS_READ = "records.read"
    ANALYSIS_VIEW = "analysis.view"
    RECORDS_EXPORT = "records.export"
    RECORDS_CREATE = "records.create"
    RECORDS_IMPORT = "records.import"
    # Ownership itself is checked by the records feature (PRD-05).
    RECORDS_MODIFY_OWN = "records.modify_own"
    RECORDS_MODIFY_ANY = "records.modify_any"
    SYSTEM_ADMIN = "system.admin"


_ALL_ROLES = (Role.ADMIN, Role.USER, Role.VIEWER)
_WRITERS = (Role.ADMIN, Role.USER)
_ADMIN_ONLY = (Role.ADMIN,)

CAPABILITY_ROLES: dict[Capability, tuple[Role, ...]] = {
    Capability.MONITORING_VIEW: _ALL_ROLES,
    Capability.RECORDS_READ: _ALL_ROLES,
    Capability.ANALYSIS_VIEW: _ALL_ROLES,
    Capability.RECORDS_EXPORT: _ALL_ROLES,
    Capability.RECORDS_CREATE: _WRITERS,
    Capability.RECORDS_IMPORT: _WRITERS,
    Capability.RECORDS_MODIFY_OWN: _WRITERS,
    Capability.RECORDS_MODIFY_ANY: _ADMIN_ONLY,
    Capability.SYSTEM_ADMIN: _ADMIN_ONLY,
}

CAPABILITY_LABELS: dict[Capability, str] = {
    Capability.MONITORING_VIEW: "檢視即時監控",
    Capability.RECORDS_READ: "讀取資料",
    Capability.ANALYSIS_VIEW: "資料分析",
    Capability.RECORDS_EXPORT: "下載 Excel",
    Capability.RECORDS_CREATE: "新增資料記錄",
    Capability.RECORDS_IMPORT: "批量導入資料",
    Capability.RECORDS_MODIFY_OWN: "修改或刪除自己建立的資料",
    Capability.RECORDS_MODIFY_ANY: "修改或刪除任何資料",
    Capability.SYSTEM_ADMIN: "系統管理",
}


def capabilities_for(role: Role) -> list[Capability]:
    return [capability for capability, roles in CAPABILITY_ROLES.items() if role in roles]


def user_can(user: User, capability: Capability) -> bool:
    return user.role in CAPABILITY_ROLES[capability]
