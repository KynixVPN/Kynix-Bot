import random


def generate_fake_id() -> int:
    return random.randint(10_000_000, 99_999_999)
