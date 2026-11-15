from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_prefix="PSI_", extra="ignore")

    base_url: str = "https://psi.nasa.gov"
    download_dir: Path = BASE_DIR.parent / "data" / "raw"  # repo-root data/raw (git-ignored)
    rag_dir: Path = BASE_DIR.parent / "data" / "rag"  # generated brief (small text, safe to commit)
    cache_dir: Path = BASE_DIR.parent / "data" / "raw" / ".summary_cache"
    max_download_mb: float = 200.0  # guard: some investigations are >10 GB in total
    cache_ttl_s: float = 600.0
    timeout_s: float = 30.0
    # Search terms for the fire-safety preset (microgravity combustion investigations)
    fire_terms: list[str] = ["saffire", "BASS", "smoldering", "flame spread", "smoke aerosol"]


settings = Settings()
