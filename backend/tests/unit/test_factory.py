import pytest

from app.adapters.memory import InlineQueue
from app.factory import ProfileNotAvailableError, build_adapters
from app.settings import Profile, Settings


def test_test_profile_builds_in_memory_adapters() -> None:
    adapters = build_adapters(Settings(profile=Profile.TEST))
    assert isinstance(adapters.queue, InlineQueue)


@pytest.mark.parametrize("profile", [Profile.LOCAL, Profile.DEPLOYED])
def test_unbuilt_profiles_fail_clearly(profile: Profile) -> None:
    with pytest.raises(ProfileNotAvailableError, match=profile.value):
        build_adapters(Settings(profile=profile))


def test_profile_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "test")
    assert Settings().profile is Profile.TEST
