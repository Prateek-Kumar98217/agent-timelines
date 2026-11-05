import pytest

from app.adapters.celery_queue import CeleryStepQueue
from app.adapters.memory import InlineQueue
from app.adapters.redis_store import RedisLiveEvents
from app.factory import ProfileNotAvailableError, build_adapters
from app.settings import Profile, Settings


def test_test_profile_builds_in_memory_adapters() -> None:
    adapters = build_adapters(Settings(profile=Profile.TEST))
    assert isinstance(adapters.queue, InlineQueue)


def test_local_profile_builds_real_infrastructure_adapters() -> None:
    adapters = build_adapters(Settings(profile=Profile.LOCAL))
    assert isinstance(adapters.queue, CeleryStepQueue)
    assert isinstance(adapters.live_events, RedisLiveEvents)
    assert adapters.auth.authenticate(None) is not None


def test_unbuilt_deployed_profile_fails_clearly() -> None:
    with pytest.raises(ProfileNotAvailableError, match="deployed"):
        build_adapters(Settings(profile=Profile.DEPLOYED))


def test_profile_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "test")
    assert Settings().profile is Profile.TEST
