from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    MEMORY_API_URL: str = "http://memory-api:8000"
    AGENT_RUNTIME_URL: str = "http://agent-runtime:9100"
    BRIDGE_SHARED_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    OAUTH_CREDENTIALS_ENCRYPTION_KEY: str = ""
    POLL_INTERVAL_SECONDS: int = 300  # 5 minutes
    LOG_LEVEL: str = "INFO"
    # Drive push webhook settings
    # Public HTTPS URL where Google Drive sends change notifications.
    # Must be reachable from the internet — typically https://api.grooveos.app/v1/drive-webhook
    DRIVE_WEBHOOK_PUBLIC_URL: str = ""
    # Random token (64+ chars) stored in drive_watch_channels.channel_token.
    # Google echoes it back in X-Goog-Channel-Token for auth verification.
    DRIVE_WEBHOOK_SECRET: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
