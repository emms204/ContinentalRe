"""Cedant/broker-specific adapters."""
from cre_cleaner.adapters.aiico_ark import AiicoArkAdapter

ADAPTERS = {
    ("AIICO", "ARK"): AiicoArkAdapter,
}

def get_adapter(cedant: str, broker: str):
    key = (cedant.upper().strip(), broker.upper().strip())
    cls = ADAPTERS.get(key)
    if cls is None:
        # fallback to AIICO ARK rules as generic v1
        return AiicoArkAdapter()
    return cls()
