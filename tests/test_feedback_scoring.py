import pytest

from feedback.interactions import get_interaction
from feedback.storage import apply_feedback_scores


def test_initial_feedback_weights_are_centralized_and_bounded():
    assert get_interaction("like").feedback_score == 1.0
    assert get_interaction("save").feedback_score == 0.8
    assert get_interaction("dislike").feedback_score == -1.0
    assert all(
        -1.0 <= get_interaction(action).feedback_score <= 1.0
        for action in ("like", "save", "dislike")
    )


def test_feedback_adjustment_is_bounded_and_resorts_candidates():
    candidates = [
        {"full_name": "org/a", "final_score": 10.0},
        {"full_name": "org/b", "final_score": 9.0},
    ]

    ranked = apply_feedback_scores(candidates, {"org/b": 0.8})

    assert ranked[0]["full_name"] == "org/b"
    assert ranked[0]["feedback_adjustment"] == pytest.approx(2.0)
    assert ranked[0]["final_score"] == pytest.approx(11.0)
    assert ranked[1]["final_score"] == pytest.approx(10.0)


def test_explicit_dislike_filters_exact_repository():
    candidates = [
        {"full_name": "org/liked", "final_score": 8.0},
        {"full_name": "org/disliked", "final_score": 12.0},
    ]

    ranked = apply_feedback_scores(candidates, {"org/disliked": -1.0})

    assert [item["full_name"] for item in ranked] == ["org/liked"]
