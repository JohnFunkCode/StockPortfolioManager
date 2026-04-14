"""
Secret and configuration loading.

Priority order for every secret:
  1. Environment variable (uppercased, hyphens → underscores)
     e.g.  jwt-secret  →  JWT_SECRET
  2. GCP Secret Manager (secret name as-is)
"""
import os
from functools import lru_cache


@lru_cache(maxsize=None)
def get_secret(name: str) -> str:
    env_key = name.upper().replace("-", "_")
    if val := os.environ.get(env_key):
        return val

    from google.cloud import secretmanager
    project = os.environ.get("GCP_PROJECT", "stock-portfolio-tfowler")
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(
        name=f"projects/{project}/secrets/{name}/versions/latest"
    )
    return response.payload.data.decode("utf-8")
