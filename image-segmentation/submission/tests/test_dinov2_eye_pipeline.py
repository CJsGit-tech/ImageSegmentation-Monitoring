import numpy as np
import pytest

import dinov2_eye_pipeline as pipeline


def candidate(x, score):
    return {"center": (x, 0), "score": score}


# --- Choosing the eyes ---


CROP = {"long_side": 100}


def test_select_eyes_with_no_candidates_returns_none():
    assert pipeline.select_eyes(CROP, []) == []


def test_select_eyes_keeps_a_plausible_pair():
    first, second = candidate(0, 1.0), candidate(20, 0.9)

    assert pipeline.select_eyes(CROP, [first, second]) == [first, second]


@pytest.mark.parametrize("gap", [5, 40])
def test_select_eyes_accepts_a_pair_on_the_spacing_limits(gap):
    assert len(pipeline.select_eyes(CROP, [candidate(0, 1.0), candidate(gap, 0.9)])) == 2


@pytest.mark.parametrize("gap", [4, 41])
def test_select_eyes_rejects_a_pair_outside_the_spacing_limits(gap):
    best = candidate(0, 1.0)

    assert pipeline.select_eyes(CROP, [best, candidate(gap, 0.9)]) == [best]


def test_select_eyes_accepts_a_second_eye_at_the_score_ratio():
    assert len(pipeline.select_eyes(CROP, [candidate(0, 1.0), candidate(20, 0.7)])) == 2


def test_select_eyes_rejects_a_second_eye_below_the_score_ratio():
    best = candidate(0, 1.0)

    assert pipeline.select_eyes(CROP, [best, candidate(20, 0.69)]) == [best]


def test_select_eyes_prefers_the_pair_with_the_highest_total_score():
    first, second, third = candidate(0, 1.0), candidate(20, 0.9), candidate(30, 0.8)

    assert pipeline.select_eyes(CROP, [third, first, second]) == [first, second]


def test_select_eyes_skips_a_top_candidate_that_has_no_partner():
    # 0.65 and 0.6 are too low to pair with 1.0, but they do pair with each other.
    top, second, third = candidate(0, 1.0), candidate(20, 0.65), candidate(40, 0.6)

    assert pipeline.select_eyes(CROP, [top, second, third]) == [second, third]


# --- Finding peaks ---


def response_with(*peaks, shape=(20, 20)):
    response = np.zeros(shape, np.float32)
    for row, column, score in peaks:
        response[row, column] = score
    return response


ALL = np.ones((20, 20), bool)


def test_find_peaks_returns_the_strongest_first():
    response = response_with((2, 2, 0.5), (15, 15, 0.9))

    assert pipeline.find_peaks(response, ALL, 0.1) == [
        (pytest.approx(0.9), 15, 15),
        (pytest.approx(0.5), 2, 2),
    ]


def test_find_peaks_ignores_scores_below_the_minimum():
    response = response_with((2, 2, 0.5), (15, 15, 0.9))

    assert [peak[1:] for peak in pipeline.find_peaks(response, ALL, 0.6)] == [(15, 15)]


def test_find_peaks_ignores_peaks_outside_the_animal():
    allowed = ALL.copy()
    allowed[15, 15] = False
    response = response_with((2, 2, 0.5), (15, 15, 0.9))

    assert [peak[1:] for peak in pipeline.find_peaks(response, allowed, 0.1)] == [(2, 2)]


def test_find_peaks_drops_the_weaker_of_two_close_peaks():
    # Three patches apart: both are local maxima, but closer than MIN_PEAK_DISTANCE.
    response = response_with((5, 5, 0.9), (5, 8, 0.8))

    assert [peak[1:] for peak in pipeline.find_peaks(response, ALL, 0.1)] == [(5, 5)]


# --- Building the reference ---


FEATURES = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]])
PIECE = np.zeros((28, 28, 3), np.uint8)  # With a 2 by 2 grid, each patch is 14 pixels square.


def outline(x1, y1, x2, y2):
    return {"polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]}


def test_eye_patches_returns_the_patch_under_an_outline():
    patches = pipeline.eye_patches(PIECE, [0, 0], [outline(14, 0, 27, 13)], FEATURES, (2, 2))

    assert patches.tolist() == [FEATURES[1].tolist()]


def test_eye_patches_moves_image_outlines_into_the_crop():
    patches = pipeline.eye_patches(PIECE, [100, 200], [outline(100, 214, 113, 227)], FEATURES, (2, 2))

    assert patches.tolist() == [FEATURES[2].tolist()]


def test_eye_patches_drops_a_patch_covered_by_exactly_half():
    # Seven of 14 columns: a patch must be more than half eye to count.
    patches = pipeline.eye_patches(PIECE, [0, 0], [outline(0, 0, 6, 13)], FEATURES, (2, 2))

    assert len(patches) == 0


def test_eye_patches_with_no_eyes_returns_nothing():
    assert len(pipeline.eye_patches(PIECE, [0, 0], [], FEATURES, (2, 2))) == 0


def test_normalized_mean_averages_all_patches_to_unit_length():
    reference = pipeline.normalized_mean([np.array([[3.0, 0.0]]), np.array([[3.0, 8.0]])])

    assert reference == pytest.approx([0.6, 0.8])
    assert np.linalg.norm(reference) == pytest.approx(1)


# --- Crops ---


def test_crop_rounds_a_box_outward():
    image = np.zeros((100, 100, 3), np.uint8)

    assert pipeline.crop(image, (10.5, 20.5, 30.5, 40.5)).shape[:2] == (21, 21)


def test_crop_limits_a_box_to_the_image():
    image = np.zeros((100, 100, 3), np.uint8)

    assert pipeline.crop(image, (-5, -5, 500, 50)).shape[:2] == (50, 100)


def test_crop_offset_matches_where_crop_starts():
    assert pipeline.crop_offset({"box": [10.5, 20.9, 30, 40]}) == [10, 20]


def test_crop_offset_is_never_negative():
    assert pipeline.crop_offset({"box": [-3.2, -0.5, 30, 40]}) == [0, 0]


def test_to_crop_returns_a_patch_centre_in_crop_pixels():
    entry = {"image": np.zeros((100, 200, 3), np.uint8), "grid": (5, 10)}

    assert pipeline.to_crop(entry, 0, 0) == pytest.approx((10, 10))
    assert pipeline.to_crop(entry, 9, 4) == pytest.approx((190, 90))
