"""
Azure Flexible Server compute SKU specifications.

Maps an Azure SKU name to (vCPU count, memory GB), which Azure states only
as a SKU name on the server itself. An unknown SKU resolves to None rather
than a guess.

Generated from Azure's capability API. To regenerate:

    az postgres flexible-server list-skus --location <region> -o json
    az mysql flexible-server list-skus --location <region> -o json

Read vCores and supportedMemoryPerVcoreMb; MySQL spells it ...PerVCoreMb.
"""

from typing import Dict, Optional, Tuple

# SKU name -> (vCPUs, memory GB).
SKU_SPECS: Dict[str, Tuple[int, float]] = {
    "Standard_B12ms": (12, 48),
    "Standard_B16ms": (16, 64),
    "Standard_B1ms": (1, 2),
    "Standard_B1s": (1, 1),
    "Standard_B20ms": (20, 80),
    "Standard_B2ms": (2, 8),
    "Standard_B2s": (2, 4),
    "Standard_B4ms": (4, 16),
    "Standard_B8ms": (8, 32),
    "Standard_D16ads_v5": (16, 64),
    "Standard_D16ads_v6": (16, 64),
    "Standard_D16ds_v4": (16, 64),
    "Standard_D16ds_v5": (16, 64),
    "Standard_D16ds_v6": (16, 64),
    "Standard_D16s_v3": (16, 64),
    "Standard_D2ads_v5": (2, 8),
    "Standard_D2ads_v6": (2, 8),
    "Standard_D2ds_v4": (2, 8),
    "Standard_D2ds_v5": (2, 8),
    "Standard_D2ds_v6": (2, 8),
    "Standard_D2s_v3": (2, 8),
    "Standard_D32ads_v5": (32, 128),
    "Standard_D32ads_v6": (32, 128),
    "Standard_D32ds_v4": (32, 128),
    "Standard_D32ds_v5": (32, 128),
    "Standard_D32ds_v6": (32, 128),
    "Standard_D32s_v3": (32, 128),
    "Standard_D48ads_v5": (48, 192),
    "Standard_D48ads_v6": (48, 192),
    "Standard_D48ds_v4": (48, 192),
    "Standard_D48ds_v5": (48, 192),
    "Standard_D48ds_v6": (48, 192),
    "Standard_D48s_v3": (48, 192),
    "Standard_D4ads_v5": (4, 16),
    "Standard_D4ads_v6": (4, 16),
    "Standard_D4ds_v4": (4, 16),
    "Standard_D4ds_v5": (4, 16),
    "Standard_D4ds_v6": (4, 16),
    "Standard_D4s_v3": (4, 16),
    "Standard_D64ads_v5": (64, 256),
    "Standard_D64ads_v6": (64, 256),
    "Standard_D64ds_v4": (64, 256),
    "Standard_D64ds_v5": (64, 256),
    "Standard_D64ds_v6": (64, 256),
    "Standard_D64s_v3": (64, 256),
    "Standard_D8ads_v5": (8, 32),
    "Standard_D8ads_v6": (8, 32),
    "Standard_D8ds_v4": (8, 32),
    "Standard_D8ds_v5": (8, 32),
    "Standard_D8ds_v6": (8, 32),
    "Standard_D8s_v3": (8, 32),
    "Standard_D96ads_v5": (96, 384),
    "Standard_D96ads_v6": (96, 384),
    "Standard_D96ds_v5": (96, 384),
    "Standard_D96ds_v6": (96, 384),
    "Standard_DC16ads_v5": (16, 64),
    "Standard_DC2ads_v5": (2, 8),
    "Standard_DC32ads_v5": (32, 128),
    "Standard_DC48ads_v5": (48, 192),
    "Standard_DC4ads_v5": (4, 16),
    "Standard_DC64ads_v5": (64, 256),
    "Standard_DC8ads_v5": (8, 32),
    "Standard_DC96ads_v5": (96, 384),
    "Standard_E16ads_v5": (16, 128),
    "Standard_E16ads_v6": (16, 128),
    "Standard_E16ds_v4": (16, 128),
    "Standard_E16ds_v5": (16, 128),
    "Standard_E16ds_v6": (16, 128),
    "Standard_E16s_v3": (16, 128),
    "Standard_E20ads_v5": (20, 160),
    "Standard_E20ads_v6": (20, 160),
    "Standard_E20ds_v4": (20, 160),
    "Standard_E20ds_v5": (20, 160),
    "Standard_E20ds_v6": (20, 160),
    "Standard_E2ads_v5": (2, 16),
    "Standard_E2ads_v6": (2, 16),
    "Standard_E2ds_v4": (2, 16),
    "Standard_E2ds_v5": (2, 16),
    "Standard_E2ds_v6": (2, 16),
    "Standard_E2s_v3": (2, 16),
    "Standard_E32ads_v5": (32, 256),
    "Standard_E32ads_v6": (32, 256),
    "Standard_E32ds_v4": (32, 256),
    "Standard_E32ds_v5": (32, 256),
    "Standard_E32ds_v6": (32, 256),
    "Standard_E32s_v3": (32, 256),
    "Standard_E48ads_v5": (48, 384),
    "Standard_E48ads_v6": (48, 384),
    "Standard_E48ds_v4": (48, 384),
    "Standard_E48ds_v5": (48, 384),
    "Standard_E48ds_v6": (48, 384),
    "Standard_E48s_v3": (48, 384),
    "Standard_E4ads_v5": (4, 32),
    "Standard_E4ads_v6": (4, 32),
    "Standard_E4ds_v4": (4, 32),
    "Standard_E4ds_v5": (4, 32),
    "Standard_E4ds_v6": (4, 32),
    "Standard_E4s_v3": (4, 32),
    "Standard_E64ads_v5": (64, 512),
    "Standard_E64ads_v6": (64, 512),
    "Standard_E64ds_v4": (64, 432),
    "Standard_E64ds_v5": (64, 512),
    "Standard_E64ds_v6": (64, 512),
    "Standard_E64s_v3": (64, 432),
    "Standard_E80ids_v4": (80, 504.0),
    "Standard_E8ads_v5": (8, 64),
    "Standard_E8ads_v6": (8, 64),
    "Standard_E8ds_v4": (8, 64),
    "Standard_E8ds_v5": (8, 64),
    "Standard_E8ds_v6": (8, 64),
    "Standard_E8s_v3": (8, 64),
    "Standard_E96ads_v5": (96, 672),
    "Standard_E96ads_v6": (96, 672),
    "Standard_E96ds_v5": (96, 672),
    "Standard_E96ds_v6": (96, 768),
    "Standard_EC16ads_v5": (16, 128),
    "Standard_EC20ads_v5": (20, 160),
    "Standard_EC2ads_v5": (2, 16),
    "Standard_EC32ads_v5": (32, 256),
    "Standard_EC48ads_v5": (48, 384),
    "Standard_EC4ads_v5": (4, 32),
    "Standard_EC64ads_v5": (64, 512),
    "Standard_EC8ads_v5": (8, 64),
    "Standard_EC96ads_v5": (96, 768),
}

MYSQL_SKU_OVERRIDES: Dict[str, Tuple[int, float]] = {
    "Standard_E64ds_v4": (64, 512),
}


def sku_specs(sku_name: str, engine: str = "") -> Optional[Tuple[int, float]]:
    """Return (vCPUs, memory GB) for an Azure SKU, or None if unknown."""
    if not sku_name:
        return None
    if engine == "mysql" and sku_name in MYSQL_SKU_OVERRIDES:
        return MYSQL_SKU_OVERRIDES[sku_name]
    return SKU_SPECS.get(sku_name)


def is_arm_sku(sku_name: str) -> bool:
    """Whether a SKU is ARM (Azure Cobalt / Ampere) rather than x86.

    ARM sizes carry a 'p' after the vCPU count, as in Standard_D4pls_v5.
    """
    if not sku_name:
        return False
    parts = sku_name.split("_")
    if len(parts) < 2:
        return False
    size = parts[1]
    digits_seen = False
    for char in size:
        if char.isdigit():
            digits_seen = True
        elif digits_seen:
            if char == "p":
                return True
            if char in ("a", "d", "s", "i", "l", "m", "t", "c", "e", "b", "n", "r"):
                continue
            break
    return False
