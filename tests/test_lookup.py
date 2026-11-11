import pytest
from helpers import combined, z

from taxengine.lookup import AddressError, normalize_street, overrides, resolve, zip_table


def test_zip_lookup_returns_state_county_city_district():
    r = resolve("TX", z("TX", 1))
    assert r.jurisdictions == ("TX", "TX-C1", "TX-CITY1", "TX-TD")
    assert r.method == "zip"


def test_split_zip_street_in_override_range_uses_other_city():
    r = resolve("NY", z("NY", 2), "120 Main St")
    assert r.method == "street_override"
    assert r.jurisdictions == ("NY", "NY-C1", "NY-CITY1", "NY-TD")
    assert r.zip_only == ("NY", "NY-C1", "NY-CITY2")
    assert r.differs_from_zip_only


def test_zip_only_lookup_charges_the_wrong_rate_for_split_zip():
    r = resolve("NY", z("NY", 2), "120 Main St")
    assert combined(r.jurisdictions) != combined(r.zip_only)
    assert combined(r.jurisdictions) - combined(r.zip_only) > 0


def test_split_zip_street_outside_range_uses_zip_default():
    r = resolve("NY", z("NY", 2), "1500 Main St")
    assert r.method == "zip" and not r.differs_from_zip_only


@pytest.mark.parametrize("num,inside", [(1, True), (999, True), (1000, False)])
def test_override_range_boundaries(num, inside):
    r = resolve("NY", z("NY", 2), f"{num} Main St")
    assert (r.method == "street_override") is inside


def test_street_names_are_normalized():
    assert normalize_street("Harbor Road") == "harbor rd"
    r = resolve("CO", z("CO", 5), "700 Harbor Road.")
    assert r.jurisdictions == ("CO", "CO-C2", "CO-CITY3", "CO-SD")


def test_same_street_in_unsplit_zip_is_not_overridden():
    assert resolve("NY", z("NY", 3), "120 Main St").method == "zip"


def test_state_without_local_tax_resolves_to_state_only():
    r = resolve("MI", z("MI", 2), "120 Main St")
    assert r.jurisdictions == ("MI",) and r.method == "state_only"


def test_unknown_zip_and_wrong_state_are_errors():
    with pytest.raises(AddressError):
        resolve("TX", "00000")
    with pytest.raises(AddressError):
        resolve("CA", z("TX", 1))


def test_every_state_has_six_zips_and_two_split_zips_where_locals_exist():
    assert len(zip_table()) == 51 * 6
    assert len(overrides()) % 2 == 0 and len(overrides()) >= 60
