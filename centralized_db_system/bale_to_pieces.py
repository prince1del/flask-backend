from __future__ import annotations

from typing import Any


def calculate_bale_to_pieces(
    total_bales: float,
    packs_per_bale: float,
    pcs_per_pack: float,
    number_of_designs: float | None = None,
    number_of_colors: float | None = None,
) -> dict[str, Any]:
    """Calculate total packs and pieces for a shipment bale breakdown.

    The calculation applies the following logic:
    - total_packs = total_bales * packs_per_bale
    - total_pieces = total_packs * pcs_per_pack
    - if designs/colors are supplied they act as logical multipliers for the
      final piece count when present.
    """

    total_bales_value = max(0.0, float(total_bales))
    packs_per_bale_value = max(0.0, float(packs_per_bale))
    pcs_per_pack_value = max(0.0, float(pcs_per_pack))

    designs = max(0.0, float(number_of_designs or 0.0))
    colors = max(0.0, float(number_of_colors or 0.0))

    total_packs = total_bales_value * packs_per_bale_value
    total_pieces = total_packs * pcs_per_pack_value

    if designs > 0:
        total_pieces *= designs
    if colors > 0:
        total_pieces *= colors

    pieces_per_bale_breakdown = {
        "bales": total_bales_value,
        "packs_per_bale": packs_per_bale_value,
        "pieces_per_pack": pcs_per_pack_value,
        "design_multiplier": designs,
        "color_multiplier": colors,
    }

    return {
        "total_bales": total_bales_value,
        "total_packs": total_packs,
        "total_pieces": total_pieces,
        "pieces_per_bale_breakdown": pieces_per_bale_breakdown,
    }
