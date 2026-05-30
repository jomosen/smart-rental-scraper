"""Unit tests for victoria_scraper parsing helpers."""
import pytest

from src.scraper.infrastructure.scrapers.victoria_scraper import (
    _parse_int,
    _parse_victoria_html,
)
from src.shared.domain.models.result import BookingResult


# ---------------------------------------------------------------------------
# _parse_int
# ---------------------------------------------------------------------------


class TestParseInt:
    def test_plain_integer(self):
        assert _parse_int("5") == 5

    def test_single_digit(self):
        assert _parse_int("2") == 2

    def test_larger_value(self):
        assert _parse_int("9") == 9

    def test_whitespace_stripped(self):
        assert _parse_int("  3  ") == 3

    def test_empty_string_returns_none(self):
        assert _parse_int("") is None

    def test_non_numeric_returns_none(self):
        assert _parse_int("AUT") is None

    def test_float_string_returns_none(self):
        assert _parse_int("5.5") is None


# ---------------------------------------------------------------------------
# BookingResult.is_confirmed_empty default
# ---------------------------------------------------------------------------

class TestBookingResultIsConfirmedEmptyDefault:
    def test_default_is_false(self):
        r = BookingResult(provider_name="test")
        assert r.is_confirmed_empty is False

    def test_explicit_true(self):
        r = BookingResult(provider_name="test", is_confirmed_empty=True)
        assert r.is_confirmed_empty is True


# ---------------------------------------------------------------------------
# _parse_victoria_html
# ---------------------------------------------------------------------------

_MSGBOX_HTML = """
<html><body>
<div id="msgbox" class="modal open">
  <div class="modal-content">
    <p>No ha sido posible encontrar una lista de precios.</p>
  </div>
  <div class="modal-footer">
    <a class="modal-action modal-close waves-effect btn victoriacarscolor">Aceptar</a>
  </div>
</div>
</body></html>
"""

_VEHICLES_HTML = """
<html><body>
<div id="cont_vehiculos">
  <div class="package-template ficha">
    <span class="nombregrupo">Hyundai i10</span>
    <span class="grupo">Economy</span>
    <div class="package-header"><h5>Basic</h5></div>
    <div class="package-price"><div><strong class="victoriacarscolor-text">25,00 €</strong></div></div>
    <strong class="precio_tot">175,00 €</strong>
    <div class="icono_container persona"><span class="valor">4</span></div>
    <div class="icono_container maleta"><span class="valor">2</span></div>
    <div class="icono_container manual"></div>
  </div>
  <div class="package-template ficha">
    <span class="nombregrupo">VW Golf</span>
    <span class="grupo">Compact</span>
    <div class="package-header"><h5>Plus</h5></div>
    <div class="package-price"><div><strong class="victoriacarscolor-text">35,50 €</strong></div></div>
    <strong class="precio_tot">248,50 €</strong>
    <div class="icono_container persona"><span class="valor">5</span></div>
    <div class="icono_container maleta"><span class="valor">3</span></div>
    <div class="icono_container automatico"></div>
  </div>
</div>
</body></html>
"""

_EMPTY_RESULTS_HTML = """<html><body><div id="cont_vehiculos"></div></body></html>"""


class TestParseVictoriaHtml:
    def test_msgbox_returns_is_confirmed_empty(self):
        result = _parse_victoria_html(_MSGBOX_HTML, "victoria")
        assert result.is_confirmed_empty is True
        assert result.cars == []

    def test_msgbox_provider_name_preserved(self):
        result = _parse_victoria_html(_MSGBOX_HTML, "victoria")
        assert result.provider_name == "victoria"

    def test_vehicles_html_not_confirmed_empty(self):
        result = _parse_victoria_html(_VEHICLES_HTML, "victoria")
        assert result.is_confirmed_empty is False

    def test_vehicles_html_parses_both_cars(self):
        result = _parse_victoria_html(_VEHICLES_HTML, "victoria")
        assert len(result.cars) == 2

    def test_first_car_fields(self):
        result = _parse_victoria_html(_VEHICLES_HTML, "victoria")
        car = result.cars[0]
        assert car.model == "Hyundai i10"
        assert car.group == "Economy"
        assert car.seats == 4
        assert car.luggage == 2
        assert car.transmission == "manual"
        assert car.rates[0].daily_price == pytest.approx(25.0, rel=1e-4)
        assert car.rates[0].total == pytest.approx(175.0, rel=1e-4)

    def test_second_car_automatic(self):
        result = _parse_victoria_html(_VEHICLES_HTML, "victoria")
        assert result.cars[1].transmission == "automatic"

    def test_empty_cont_vehiculos_returns_empty_cars(self):
        result = _parse_victoria_html(_EMPTY_RESULTS_HTML, "victoria")
        assert result.cars == []
        assert result.is_confirmed_empty is False
