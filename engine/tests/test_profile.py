"""The profile loader refuses anything that could silently load fewer terms."""

import json

import leakmatrix as lm
import pytest

from docsan.profile import (
    ProfileError,
    add_classifications,
    load_profile,
    parse_profile,
)

GOOD = json.loads(lm.PROFILE.read_text(encoding="utf-8"))


def variant(**changes):
    data = json.loads(json.dumps(GOOD))
    data.update(changes)
    return data


@pytest.mark.parametrize(
    "data",
    [
        variant(term=[]),
        variant(terms=[]),
        variant(terms=[{"text": "Aurelan", "label": "COMPANY"}]),
        variant(terms=[{"text": "Aurelan", "label": "CLIENT", "case_sensitive": "yes"}]),
        variant(terms=[{"text": "Aurelan", "label": "CLIENT", "colour": "red"}]),
        variant(terms=[{"text": " ", "label": "CLIENT"}]),
        variant(exceptions=[{"text": "Unknown", "followed_by": ["x"]}]),
        variant(exceptions=[{"text": "PATCH", "followed_by": ["("]}]),
        variant(redact_system_names="yes"),
        variant(version=2),
        variant(format="other"),
        [],
    ],
)
def test_invalid_profiles_are_refused(data):
    with pytest.raises(ProfileError):
        parse_profile(data)


def test_good_profile_loads():
    profile = load_profile(lm.PROFILE)
    assert profile.name == "Aurelan Group"
    assert any(t.text == "PATCH" and t.case_sensitive for t in profile.terms)


def test_classifications_are_recorded(tmp_path):
    path = lm.write_profile(tmp_path / "profile.json")
    add_classifications(path, {"Halden": "SITE"}, ["setup.py"])
    profile = load_profile(path)
    assert any(t.text == "Halden" and t.label == "SITE" for t in profile.terms)
    assert "setup.py" in profile.safe_words
    assert list(tmp_path.glob(".profile-*")) == []
