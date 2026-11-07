from tm_shared.health import health


def test_health_ok():
    h = health("api")
    assert h.service == "api"
    assert h.status == "ok"
    assert h.version
