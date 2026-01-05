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


    XUI_TLS_CA_CERT: str | None = None
    XUI_TLS_CLIENT_CERT: str | None = None
    XUI_TLS_CLIENT_KEY: str | None = None
    XUI_TLS_FINGERPRINT_SHA256: str | None = None

    INSTRUCTION_URL: str
    PRIVACY_URL: str
    TERMS_URL: str

    CODE_HASH: str       
    HASH_SALT: str
    MEMORY_CLEAN_INTERVAL_HOURS: int = 6
    PROVIDER_TOKEN: str | None = None

    @field_validator("CODE_HASH", mode="before")
    @classmethod
    def validate_code_hash(cls, v: str) -> str:
        if v is None:
            raise ValueError("CODE_HASH is required but not set")
        v = str(v).strip()
        if not v:
            raise ValueError("CODE_HASH cannot be empty")
        return v

    @field_validator("XUI_TLS_FINGERPRINT_SHA256", mode="before")
    @classmethod
    def validate_xui_fingerprint(cls, v):
        if v is None:
            return None
        s = str(v).strip().lower()
        if not s:
            return None
        s = s.replace(":", "").replace(" ", "")
        if len(s) != 64 or any(c not in "0123456789abcdef" for c in s):
            raise ValueError("XUI_TLS_FINGERPRINT_SHA256 must be a SHA256 hex fingerprint (64 hex chars)")
        return s

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
