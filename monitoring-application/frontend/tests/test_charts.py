import pytest

from core import charts
from core.charts import Bounds


def test_bounds_of_reads_a_range():
    assert charts.bounds_of({"min": 1, "max": 5}) == Bounds(1.0, 5.0)


@pytest.mark.parametrize("range_", [None, {}])
def test_bounds_of_without_a_range(range_):
    assert charts.bounds_of(range_) is None


def test_y_domain_with_no_data_is_a_default_range():
    assert charts._y_domain([], None) == [0.0, 1.0]


def test_y_domain_pads_the_values():
    assert charts._y_domain([0.0, 100.0], None) == pytest.approx([-18, 118])


def test_y_domain_always_includes_the_bounds():
    low, high = charts._y_domain([5.0], Bounds(0.0, 10.0))

    assert low < 0 and high > 10


def test_y_domain_always_includes_values_outside_the_bounds():
    low, high = charts._y_domain([-50.0, 200.0], Bounds(0.0, 10.0))

    assert low < -50 and high > 200


def test_y_domain_with_one_value_still_has_height():
    assert charts._y_domain([10.0], None) == pytest.approx([9, 11])


def test_y_domain_with_only_zero_still_has_height():
    assert charts._y_domain([0.0], None) == pytest.approx([-1, 1])
