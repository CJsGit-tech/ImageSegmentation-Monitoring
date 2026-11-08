from run_yoloe_test_samples import owner_of, remove_duplicates

SHAPE = (200, 200, 3)


def detection(x1, y1, x2, y2, confidence=0.5):
    return {"polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], "confidence": confidence}


def eye_at(x, y):
    return {"box": [x - 2, y - 2, x + 2, y + 2]}


def test_remove_duplicates_keeps_the_more_confident_of_two_matching_masks():
    low, high = detection(10, 10, 50, 50, 0.3), detection(10, 10, 50, 50, 0.9)

    assert remove_duplicates([low, high], SHAPE, 0.6) == [high]


def test_remove_duplicates_removes_a_partial_outline_inside_a_full_one():
    full, partial = detection(10, 10, 100, 100, 0.9), detection(20, 20, 40, 40, 0.5)

    assert remove_duplicates([partial, full], SHAPE, 0.6) == [full]


def test_remove_duplicates_keeps_animals_that_only_overlap():
    left, right = detection(0, 0, 100, 100, 0.9), detection(80, 0, 180, 100, 0.8)

    assert remove_duplicates([left, right], SHAPE, 0.6) == [left, right]


def test_remove_duplicates_keeps_separate_detections_in_confidence_order():
    low, high = detection(0, 0, 20, 20, 0.2), detection(100, 100, 120, 120, 0.8)

    assert remove_duplicates([low, high], SHAPE, 0.6) == [high, low]


def test_remove_duplicates_drops_a_detection_outside_the_image():
    assert remove_duplicates([detection(500, 500, 600, 600)], SHAPE, 0.6) == []


ANIMALS = [detection(0, 0, 50, 50), detection(100, 0, 150, 50)]


def test_owner_is_the_animal_under_the_eye():
    assert owner_of(eye_at(125, 25), ANIMALS, 10) == 1


def test_owner_may_be_just_outside_the_outline():
    assert owner_of(eye_at(55, 25), ANIMALS, 10) == 0


def test_eye_beyond_the_limit_has_no_owner():
    assert owner_of(eye_at(75, 25), ANIMALS, 10) is None


def test_eye_with_no_animals_has_no_owner():
    assert owner_of(eye_at(25, 25), [], 10) is None


def test_eye_inside_two_animals_belongs_to_the_one_it_is_deepest_in():
    overlapping = [detection(0, 0, 100, 100), detection(40, 0, 200, 100)]

    assert owner_of(eye_at(90, 50), overlapping, 10) == 1
