from pathlib import Path
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
    # Mount prefix when the app is served under a subpath (e.g. "/story").
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


settings = Settings()
