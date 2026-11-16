"""The tools of the gateway. `build_gateway` registers them all on one store."""

from pathlib import Path

from app.docintel.reader import Reader
from app.store import Store
from app.tools import checks, claims, documents, policies, requirements, routing
from app.tools.gateway import Gateway


def build_gateway(store: Store, reader: Reader, uploads: Path) -> Gateway:
    """Every tool, on this store, with this reader and this folder of files. The app
    builds one at startup; the kit run of `app/numbers.py` builds one of its own."""
    gateway = Gateway(store)
    policies.register(gateway)
    documents.register(gateway, reader, uploads)
    claims.register(gateway)
    checks.register(gateway, uploads)
    requirements.register(gateway)
    routing.register(gateway)
    return gateway
