from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    BOT_TOKEN: str
    ADMINS: List[int]

    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "kynix"

    XUI_BASE_URL: str
    XUI_USERNAME: str
    XUI_PASSWORD: str
    XUI_INBOUND_ID: int
    XUI_INBOUND_ID_INF: int

    INSTRUCTION_URL: str
    PRIVACY_URL: str
    TERMS_URL: str

    CODE_HASH: str          # обязателен
    HASH_SALT: str
    MEMORY_CLEAN_INTERVAL_HOURS: int = 6
    PROVIDER_TOKEN: str | None = None

    @field_validator("CODE_HASH", mode="before")
    @classmethod
    def validate_code_hash(cls, v: str) -> str:
        # Нельзя без CODE_HASH и нельзя пустую строку
        if v is None:
            raise ValueError("CODE_HASH is required but not set")
        v = str(v).strip()
        if not v:
            raise ValueError("CODE_HASH cannot be empty")
        return v

    @field_validator("ADMINS", mode="before")
    @classmethod
    def parse_admins(cls, v):
        if isinstance(v, list):
            return [int(x) for x in v]

        s = str(v)
        s = s.replace("[", "").replace("]", "").replace(" ", "")
        parts = s.split(",")

        return [int(x) for x in parts if x.isdigit()]


settings = Settings()
ADMINS = settings.ADMINS
