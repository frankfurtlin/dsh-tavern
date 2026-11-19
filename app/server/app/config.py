from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://reader:reader@localhost:55432/reader_test"
    jwt_signing_secret: str
    provider_master_key: str
    ai_mode: str = "real"
    provider_allowed_hosts: str = ""
    provider_http_proxy: str = ""
    cors_origins: str = "http://127.0.0.1:8810,http://localhost:8810,capacitor://localhost,http://localhost,https://localhost"
    registration_enabled: bool = True
    product_unlimited_usage: bool = False
    cookie_secure: bool = False
    # Mount prefix when the app is served under a subpath (e.g. "/tavern").
    # Cookies must carry the public path or browsers never send them back.
    cookie_path_prefix: str = ""
    storage_path: Path = Path("data/private")
    access_minutes: int = 15
    refresh_days: int = 30
    interactive_world_enabled: bool = False
    world_task_concurrency: int = 4
    scene_image_concurrency: int = 3
    scene_vision_concurrency: int = 4
    scene_vision_start_interval_seconds: float = 1.25
    product_public_origin: str = ""
    product_storage_driver: str = "local"
    product_s3_endpoint: str = ""
    product_s3_access_key: str = ""
    product_s3_secret_key: str = ""
    product_s3_bucket: str = "private-data"
    product_s3_secure: bool = True
    product_payment_provider: str = "disabled"
    product_payment_environment: str = "test"
    product_i6ls_base_url: str = ""
    product_i6ls_merchant: str = ""
    product_i6ls_private_key: str = ""
    product_i6ls_public_key: str = ""
    product_payment_allowed_hosts: str = "pancake.waffo.ai"
    product_waffo_service_url: str = "http://127.0.0.1:3010"
    product_waffo_service_token: str = ""
    # DSH plugin host authenticates the browser and proxies "/tavern/**" with a
    # shared secret header. When enabled, that header replaces app-side login.
    trusted_auth: bool = Field(False, validation_alias=AliasChoices("DSH_TRUSTED_AUTH", "TRUSTED_AUTH"))
    trusted_username: str = Field("", validation_alias=AliasChoices("DSH_TRUSTED_USERNAME", "TRUSTED_USERNAME"))
    trusted_secret: str = Field("", validation_alias=AliasChoices("DSH_TRUSTED_SECRET", "TRUSTED_SECRET"))
    # DSH model gateway (docs/step2-dsh-generation.md). The gateway runs inside
    # the DSH process and reuses its logged-in model selection; the app only
    # needs this local URL and the shared secret.
    dsh_agent_url: str = Field("http://127.0.0.1:3080/tavern-agent/v1", validation_alias=AliasChoices("DSH_AGENT_URL", "AGENT_URL"))
    dsh_agent_secret: str = Field("", validation_alias=AliasChoices("DSH_AGENT_SECRET", "AGENT_SECRET"))
    dsh_agent_timeout_seconds: float = Field(120, validation_alias=AliasChoices("DSH_AGENT_TIMEOUT_SECONDS", "AGENT_TIMEOUT_SECONDS"))
    # 可选：显式指定网关使用的模型，形如 ``provider/model``。DSH 的默认模型可能
    # 被切到某个未给 /tavern-agent 这一路配好凭据的 provider（例如 tencent），
    # 此时由应用侧覆盖，不必重启 DSH 宿主。留空则沿用 DSH 当前默认模型。
    dsh_agent_model: str = Field("", validation_alias=AliasChoices("DSH_AGENT_MODEL", "AGENT_MODEL"))


settings = Settings()
