import pytest

from app.adapters.celery_queue import CeleryStepQueue, make_celery
from app.adapters.llm import REQUEST_TIMEOUT_SECONDS
from app.adapters.mcp_tools import McpToolExecutor
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
    assert isinstance(adapters.tools, McpToolExecutor)
    assert adapters.auth.authenticate(None) is not None


def test_unbuilt_deployed_profile_fails_clearly() -> None:
    with pytest.raises(ProfileNotAvailableError, match="deployed"):
        build_adapters(Settings(profile=Profile.DEPLOYED))


def test_profile_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "test")
    assert Settings().profile is Profile.TEST


def test_a_step_is_stopped_before_it_can_outlive_its_lease() -> None:
    settings = Settings()
    assert make_celery(settings).conf.task_time_limit <= settings.lease_seconds


def test_the_lease_outlasts_the_slowest_llm_call() -> None:
    assert Settings().lease_seconds > REQUEST_TIMEOUT_SECONDS
