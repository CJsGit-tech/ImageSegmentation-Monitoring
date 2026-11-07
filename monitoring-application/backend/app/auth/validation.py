"""Account and password format rules. Problems never contain the submitted value."""

import re
import unicodedata

from app.models.user import USERNAME_MAX_LENGTH

USERNAME_MIN_LENGTH = 2
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

# \w is Unicode aware, so Traditional Chinese and other scripts are allowed.
_USERNAME_CHARACTERS = re.compile(r"[\w.@-]*")


def normalize_username(username: str) -> str:
    """Folds compatibility forms, so a full-width Ａ and a plain A are the same account."""
    return unicodedata.normalize("NFKC", username)


def _problem(field: str, problem_type: str, message: str) -> dict[str, str]:
    return {"field": f"body.{field}", "message": message, "type": problem_type}


def username_problems(username: str) -> list[dict[str, str]]:
    """Expects a normalized username."""
    problems = []
    if len(username) < USERNAME_MIN_LENGTH:
        problems.append(_problem("username", "username_too_short", f"帳號至少需要 {USERNAME_MIN_LENGTH} 個字元"))
    if len(username) > USERNAME_MAX_LENGTH:
        problems.append(_problem("username", "username_too_long", f"帳號最多 {USERNAME_MAX_LENGTH} 個字元"))
    if not _USERNAME_CHARACTERS.fullmatch(username):
        problems.append(
            _problem("username", "username_invalid_characters", "帳號只能包含文字、數字與 _ . - @，不可有空白")
        )
    return problems


def password_problems(password: str) -> list[dict[str, str]]:
    problems = []
    if len(password) < PASSWORD_MIN_LENGTH:
        problems.append(_problem("password", "password_too_short", f"密碼至少需要 {PASSWORD_MIN_LENGTH} 個字元"))
    if len(password) > PASSWORD_MAX_LENGTH:
        problems.append(_problem("password", "password_too_long", f"密碼最多 {PASSWORD_MAX_LENGTH} 個字元"))
    if not any("A" <= char <= "Z" for char in password):
        problems.append(_problem("password", "password_missing_uppercase", "密碼缺少英文大寫字母"))
    if not any("a" <= char <= "z" for char in password):
        problems.append(_problem("password", "password_missing_lowercase", "密碼缺少英文小寫字母"))
    if not any("0" <= char <= "9" for char in password):
        problems.append(_problem("password", "password_missing_digit", "密碼缺少數字"))
    if not any(not char.isalnum() and not char.isspace() for char in password):
        problems.append(_problem("password", "password_missing_symbol", "密碼缺少符號"))
    return problems
