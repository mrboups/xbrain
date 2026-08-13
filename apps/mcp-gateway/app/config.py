from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    MEMORY_API_URL: str = "http://memory-api:8000"
    # NO DEFAULT, deliberately — matches memory-api's app/config.py:16. This
    # secret is a VERIFICATION key here (app/auth.py decodes bridge JWTs with
    # it), so an empty value does not degrade to "signing disabled": it means
    # every token signed with the empty string verifies, and the gateway then
    # forwards the attacker's X-Team-Scope to every sidecar. A missing var must
    # stop the container at boot, where it is visible, rather than open the door
    # quietly. docker-compose already passes it.
    BRIDGE_SHARED_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    GOOGLE_CLIENT_ID: str = ""
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
