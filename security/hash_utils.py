import hashlib
from argon2.low_level import hash_secret_raw, Type as Argon2Type
from config import settings


def _get_salt() -> bytes:
    salt = settings.HASH_SALT.encode()

    if len(salt) != 32:
        raise ValueError(
            f"HASH_SALT must be exactly 32 bytes long, got {len(salt)} bytes."
        )

    return salt


def hash_tg_id(real_id: int | str) -> str:
    real_id = str(real_id)

    fp = hashlib.sha256(real_id.encode()).hexdigest().encode()

    hashed = hash_secret_raw(
        secret=fp,
        salt=_get_salt(),
        time_cost=3,
        memory_cost=65536,
        parallelism=1,
        hash_len=32,
        type=Argon2Type.ID
    )

    return hashed.hex()
