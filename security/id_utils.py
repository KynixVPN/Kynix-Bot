import random


def generate_fake_id() -> int:
    # 8-digit numeric id
    return random.randint(10_000_000, 99_999_999)
