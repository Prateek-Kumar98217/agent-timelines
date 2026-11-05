import pytest

from browser_service.policy import public_web_only


def resolver(*addresses: str):  # type: ignore[no-untyped-def]
    return lambda _host: list(addresses)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.8",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert public_web_only("http://example.test/", resolver(address))


def test_one_private_address_among_public_ones_is_refused() -> None:
    assert public_web_only("http://example.test/", resolver("93.184.216.34", "10.0.0.1"))


def test_public_addresses_are_allowed() -> None:
    assert public_web_only("https://example.test/a?b=1", resolver("93.184.216.34")) is None
    assert public_web_only("http://example.test/", resolver("2606:2800:220:1::1")) is None


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.test/", "javascript:alert(1)", "data:text/html,x", "x"],
)
def test_other_schemes_are_refused(url: str) -> None:
    assert public_web_only(url, resolver("93.184.216.34"))


def test_credentials_in_urls_are_refused() -> None:
    assert public_web_only("http://user:pw@example.test/", resolver("93.184.216.34"))


def test_unresolvable_hosts_are_refused() -> None:
    def fail(_host: str) -> list[str]:
        raise OSError("no such host")

    assert public_web_only("http://missing.test/", fail)
    assert public_web_only("http://missing.test/", resolver())


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/", "http://localhost/", "http://[::1]/", "http://2130706433/"]
)
def test_literal_local_hosts_are_refused_with_the_real_resolver(url: str) -> None:
    assert public_web_only(url)
