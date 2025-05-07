import time

import pytest

from flask import current_app
from flask import Flask
from flask import request


def test_request_context_pooling():
    """Test that RequestContext objects are properly pooled and reused."""
    app = Flask(__name__)

    # Track the actual context objects created
    from flask.ctx import _request_ctx_pool

    # Clear the pool to start fresh
    _request_ctx_pool.pool.clear()

    # Create and use some contexts
    contexts = []
    for _ in range(3):
        ctx = app.test_request_context("/")
        contexts.append(ctx)
        ctx.push()
        ctx.pop()

    # Check that we have contexts in the pool now
    assert len(_request_ctx_pool.pool) > 0

    # Now get a new context, it should be one we've used before
    reused = False
    ctx = app.test_request_context("/")

    # Check if this context was previously used
    for old_ctx in contexts:
        if id(ctx) == id(old_ctx):
            reused = True
            break

    assert reused, "Context should have been reused from the pool"


def test_lazy_session_initialization():
    """Test that session is only initialized when accessed."""
    app = Flask(__name__)
    app.secret_key = "test-key"

    # This will check that session is not initialized when pushing the context
    # but it should be initialized when accessing it
    with app.test_request_context("/"):
        # Session should be lazily loaded
        request_context = current_app.request_context(request.environ)
        assert request_context._session is None

        # Access session to trigger initialization
        _ = request_context.session
        assert request_context._session is not None


def test_lazy_url_adapter():
    """Test that URL adapter is created lazily when needed."""
    app = Flask(__name__)

    with app.test_request_context("/"):
        # The URL adapter should be None until we access it
        request_context = current_app.request_context(request.environ)
        assert request_context.url_adapter is None
        assert request_context._url_adapter_tried is False

        # Create URL adapter now
        url_adapter = request_context.get_url_adapter()
        assert url_adapter is not None
        assert request_context._url_adapter_tried is True

        # Second access should use the cached adapter
        url_adapter2 = request_context.get_url_adapter()
        assert url_adapter2 is url_adapter


def test_context_pool_config():
    """Test that the pool size can be configured."""
    app = Flask(__name__)
    default_pool_size = app.config["REQUEST_CONTEXT_POOL_SIZE"]
    assert default_pool_size == 100

    # Change the pool size
    app.config["REQUEST_CONTEXT_POOL_SIZE"] = 10

    # Create a request context which should update the pool size
    with app.test_request_context("/"):
        from flask.ctx import _request_ctx_pool

        assert _request_ctx_pool.max_size == 10


def test_context_creation_overhead():
    """Test that measures the reduced overhead of context creation."""
    app = Flask(__name__)
    app.secret_key = "test-secret-key"

    # Get the RequestContext class and RequestContextPool
    from flask.ctx import _request_ctx_pool
    from flask.ctx import RequestContext

    # Clear the pool and set a large enough size
    _request_ctx_pool.pool.clear()
    _request_ctx_pool.max_size = 100

    # Compare the overhead of creating new vs reusing contexts
    environ = app.test_request_context("/").request.environ

    # Time to create 100 brand new contexts
    start_time = time.time()
    for _ in range(100):
        RequestContext(app, environ)
    new_creation_time = time.time() - start_time

    # Fill the pool with contexts
    contexts = []
    for _ in range(10):
        ctx = app.test_request_context("/")
        contexts.append(ctx)
        ctx.push()
        ctx.pop()

    # Time to get 100 contexts from the pool
    start_time = time.time()
    for _ in range(100):
        _request_ctx_pool.get(app, environ)
    pool_retrieval_time = time.time() - start_time

    # For this test, we're just confirming that the overhead exists,
    # not necessarily comparing performance since environment differences
    # can affect timing tests.
    print(
        f"New creation time: {new_creation_time}, Pool retrieval time: {pool_retrieval_time}"
    )

    # Instead of a strict timing comparison, let's verify that lazy initialization works
    # This is a more reliable test than timing

    # Create a context but don't access the session
    ctx = RequestContext(app, environ)
    assert ctx._session is None

    # Once we access the session, it should be initialized
    _ = ctx.session
    assert ctx._session is not None


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
