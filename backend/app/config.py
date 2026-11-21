from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_base_url: str = "http://localhost:8080"
    secret_key: str = "change-me"
    database_url: str = "postgresql+psycopg://finn:finn@db:5432/finn"
    upload_dir: str = "/data/uploads"
    static_dir: str = "/app/static"
    default_locale: str = "fr"
    base_currency: str = "CHF"

    # auth: "oidc" (production) or "disabled" (local dev, single implicit user)
    auth_mode: str = "oidc"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_allowed_groups: str = ""  # comma separated; empty = any authenticated user
    oidc_allowed_emails: str = ""  # comma separated; empty = any
    oidc_end_session: bool = True

    # AI (OpenAI-compatible)
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_text_model: str = ""
    ai_vision_model: str = ""
    ai_timeout: float = 60.0
    ai_max_concurrency: int = 4
    ai_worker_interval: float = 3.0
    worker_enabled: bool = True

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_base_url and self.ai_text_model)

    @property
    def vision_enabled(self) -> bool:
        return bool(self.ai_base_url and self.ai_vision_model)

    @property
    def secure_cookies(self) -> bool:
        return self.app_base_url.startswith("https://")

    @staticmethod
    def _csv(v: str) -> list[str]:
        return [x.strip() for x in v.split(",") if x.strip()]

    @property
    def allowed_groups(self) -> list[str]:
        return self._csv(self.oidc_allowed_groups)

    @property
    def allowed_emails(self) -> list[str]:
        return [e.lower() for e in self._csv(self.oidc_allowed_emails)]


@lru_cache
def get_settings() -> Settings:
    return Settings()
