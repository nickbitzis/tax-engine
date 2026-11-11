"""Product catalog for Larkspur Supply Co., the made-up retailer. Prices in cents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Product:
    sku: str
    name: str
    tax_code: str
    price: int
    holiday: str | None = None  # holiday category: clothing, school_supplies, computers, emergency


PRODUCTS: dict[str, Product] = {p.sku: p for p in [
    Product("LAMP-01", "Desk lamp", "GEN", 3_999),
    Product("SKIL-01", "Cast iron skillet", "GEN", 4_500),
    Product("SPKR-01", "Bluetooth speaker", "GEN", 7_999),
    Product("DESK-01", "Standing desk", "GEN", 34_900),
    Product("CHAIR-01", "Office chair", "GEN", 22_900),
    Product("TV-55", "55 inch TV", "GEN", 47_900),
    Product("MOUNT-01", "TV wall mount", "GEN", 6_999),
    Product("BPAK-01", "Backpack", "GEN", 5_400, "school_supplies"),
    Product("NOTE-03", "Notebook 3 pack", "GEN", 899, "school_supplies"),
    Product("PENS-12", "Gel pens 12 pack", "GEN", 699, "school_supplies"),
    Product("LAPT-14", "14 inch laptop", "GEN", 89_900, "computers"),
    Product("TAB-11", "11 inch tablet", "GEN", 32_900, "computers"),
    Product("FLSH-01", "LED flashlight", "GEN", 2_499, "emergency"),
    Product("GENR-01", "Portable generator", "GEN", 64_900, "emergency"),
    Product("TEE-01", "Cotton T-shirt", "CLOTH", 1_800, "clothing"),
    Product("JEAN-01", "Selvedge jeans", "CLOTH", 6_800, "clothing"),
    Product("SWTR-01", "Wool sweater", "CLOTH", 9_800, "clothing"),
    Product("SHOE-01", "Running shoes", "CLOTH", 11_500, "clothing"),
    Product("JKT-01", "Rain jacket", "CLOTH", 12_900, "clothing"),
    Product("COAT-01", "Winter coat", "CLOTH", 24_900, "clothing"),
    Product("SOCK-06", "Socks 6 pack", "CLOTH", 1_400, "clothing"),
    Product("COFF-2", "Coffee beans 2 lb", "GROC", 2_400),
    Product("OIL-01", "Olive oil 1 l", "GROC", 1_800),
    Product("GRAN-04", "Granola 4 pack", "GROC", 1_600),
    Product("RX-REFILL", "Prescription refill", "RX", 3_500),
    Product("RX-PEN", "Insulin pen (prescription)", "RX", 7_800),
    Product("EBOOK-01", "E-book", "DIGITAL", 1_299),
    Product("ALBUM-01", "Music album download", "DIGITAL", 999),
    Product("APP-PHOTO", "Photo app license (download)", "DIGITAL", 5_999),
    Product("SAAS-STOR", "Cloud storage, 1 year", "SAAS", 9_900),
    Product("SAAS-EDIT", "Photo editor subscription, 1 month", "SAAS", 1_499),
    Product("SVC-MOUNT", "TV mounting service", "INSTALL", 12_900),
    Product("SVC-DESK", "Desk assembly service", "INSTALL", 7_900),
]}
