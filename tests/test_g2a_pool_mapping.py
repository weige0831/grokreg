from grokreg.sink.grok2api import resolve_pool_api_value


def test_build_default():
    assert resolve_pool_api_value({"grok2api_pool_name": "Build"}) == "build"


def test_explicit_api_value():
    assert (
        resolve_pool_api_value(
            {"grok2api_pool_name": "Build", "grok2api_pool_api_value": "Build"}
        )
        == "Build"
    )


def test_never_silent_basic_for_unknown():
    # custom pool name should pass through, not become basic
    assert resolve_pool_api_value({"grok2api_pool_name": "MyPool"}) == "MyPool"


def test_basic_alias():
    assert resolve_pool_api_value({"grok2api_pool_name": "ssoBasic"}) == "basic"
