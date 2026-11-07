"""Argon2id password hashing, run off the event loop."""

import anyio
from pwdlib import PasswordHash

_hasher = PasswordHash.recommended()

# Each hash takes about 64 MiB, so concurrency is capped well below the default thread limit.
_limiter = anyio.CapacityLimiter(4)

# Verified when the account does not exist, so that both login failures cost the same.
_DUMMY_HASH = _hasher.hash("account-does-not-exist")


async def hash_password(password: str) -> str:
    return await anyio.to_thread.run_sync(_hasher.hash, password, limiter=_limiter)


async def verify_password(password: str, password_hash: str) -> bool:
    return await anyio.to_thread.run_sync(_hasher.verify, password, password_hash, limiter=_limiter)


async def verify_against_dummy(password: str) -> None:
    await verify_password(password, _DUMMY_HASH)
