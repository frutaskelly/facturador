"""Exporta el contrato OpenAPI del backend a backend/openapi.json.

Es la fuente de la que el frontend genera sus tipos (`npm run gen:api` →
lib/api-types.gen.ts). No necesita base de datos ni servidor corriendo: solo
importa la app. Correr tras cambiar schemas/endpoints y commitear el resultado
(CI verifica que no quede desfasado).

Uso:
    python -m scripts.export_openapi
"""
import json
import os
from pathlib import Path

# Env mínimo para que settings valide sin un .env real (igual que conftest).
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault(
    "SUPABASE_JWKS_URL", "https://example.supabase.co/auth/v1/.well-known/jwks.json"
)

from app.main import app  # noqa: E402


def main() -> None:
    spec = app.openapi()
    out = Path(__file__).resolve().parent.parent / "openapi.json"
    out.write_text(json.dumps(spec, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"{out} ({len(spec.get('paths', {}))} rutas, {len(spec.get('components', {}).get('schemas', {}))} schemas)")


if __name__ == "__main__":
    main()
