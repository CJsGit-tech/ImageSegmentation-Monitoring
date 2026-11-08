import pytest

from measure_yoloe_eyes import animals_with_eyes, centroid, distance, measure_both_eyes, measure_right_eye_pairs


def square(x, y, size=2):
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size]]


def eye(x, y, animal_index, confidence=0.5):
    return {"polygon": square(x, y), "animal_index": animal_index, "confidence": confidence}


def test_centroid_of_a_square_is_its_middle():
    assert centroid(square(10, 20)) == pytest.approx((11, 21))


def test_centroid_does_not_depend_on_point_order():
    assert centroid(square(10, 20)[::-1]) == pytest.approx((11, 21))


def test_centroid_weights_by_area_rather_than_by_point():
    # A right triangle's centroid is a third of the way along each leg, not its points' middle.
    assert centroid([[0, 0], [6, 0], [0, 6]]) == pytest.approx((2, 2))


def test_centroid_of_points_on_one_line_is_their_average():
    assert centroid([[0, 0], [2, 2], [4, 4]]) == pytest.approx((2, 2))


def test_distance_uses_both_axes():
    assert distance((1, 1), (4, 5)) == pytest.approx(5)


def test_animals_keep_only_their_own_eyes_ordered_by_x():
    prediction = {
        "animals": [{"class": "cat"}, {"class": "dog"}],
        "eyes": [eye(50, 0, 0), eye(10, 0, 0), eye(90, 0, 1)],
    }

    cat, dog = animals_with_eyes(prediction)

    assert cat["label"] == "cat #0"
    assert cat["eyes"] == [pytest.approx((11, 1)), pytest.approx((51, 1))]
    assert dog["label"] == "dog #1"
    assert dog["found"] == 1


def test_animal_with_three_eyes_keeps_the_two_most_confident():
    prediction = {
        "animals": [{"class": "cat"}],
        "eyes": [eye(10, 0, 0, 0.9), eye(50, 0, 0, 0.1), eye(90, 0, 0, 0.8)],
    }

    cat = animals_with_eyes(prediction)[0]

    assert cat["found"] == 2
    assert cat["eyes"] == [pytest.approx((11, 1)), pytest.approx((91, 1))]


def test_both_eyes_prints_the_distance(capsys):
    measure_both_eyes([{"label": "cat #0", "eyes": [(0, 0), (3, 4)], "found": 2}])

    assert "= 5.00 px" in capsys.readouterr().out


def test_both_eyes_reports_an_animal_it_cannot_measure(capsys):
    measure_both_eyes([{"label": "cat #0", "eyes": [(0, 0)], "found": 1}])

    output = capsys.readouterr().out
    assert "cannot measure: 1 eye(s) detected" in output
    assert " px" not in output


def test_right_eye_pairs_measure_each_animals_first_eye(capsys):
    measure_right_eye_pairs([
        {"label": "cat #0", "eyes": [(0, 0), (100, 100)], "found": 2},
        {"label": "dog #1", "eyes": [(6, 8), (200, 200)], "found": 2},
    ])

    output = capsys.readouterr().out
    assert "cat #0 to dog #1" in output
    assert "= 10.00 px" in output


def test_right_eye_pairs_skip_an_animal_with_one_eye(capsys):
    measure_right_eye_pairs([
        {"label": "cat #0", "eyes": [(0, 0), (1, 1)], "found": 2},
        {"label": "dog #1", "eyes": [(6, 8)], "found": 1},
    ])

    output = capsys.readouterr().out
    assert "dog #1: skipped" in output
    assert "cannot measure: fewer than 2 animals" in output
    assert " px" not in output
