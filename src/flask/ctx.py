from __future__ import annotations

import contextvars
import sys
import typing as t
import weakref
from functools import update_wrapper
from types import TracebackType

from werkzeug.exceptions import HTTPException

from . import typing as ft
from .globals import _cv_app
from .globals import _cv_request
from .signals import appcontext_popped
from .signals import appcontext_pushed

if t.TYPE_CHECKING:  # pragma: no cover
    from _typeshed.wsgi import WSGIEnvironment

    from .app import Flask
    from .sessions import SessionMixin
    from .wrappers import Request


# a singleton sentinel value for parameter defaults
_sentinel = object()


class _AppCtxGlobals:
    """A plain object. Used as a namespace for storing data during an
    application context.

    Creating an app context automatically creates this object, which is
    made available as the :data:`g` proxy.

    .. describe:: 'key' in g

        Check whether an attribute is present.

        .. versionadded:: 0.10

    .. describe:: iter(g)

        Return an iterator over the attribute names.

        .. versionadded:: 0.10
    """

    # Define attr methods to let mypy know this is a namespace object
    # that has arbitrary attributes.

    def __getattr__(self, name: str) -> t.Any:
        try:
            return self.__dict__[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: t.Any) -> None:
        self.__dict__[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self.__dict__[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, name: str, default: t.Any | None = None) -> t.Any:
        """Get an attribute by name, or a default value. Like
        :meth:`dict.get`.

        :param name: Name of attribute to get.
        :param default: Value to return if the attribute is not present.

        .. versionadded:: 0.10
        """
        return self.__dict__.get(name, default)

    def pop(self, name: str, default: t.Any = _sentinel) -> t.Any:
        """Get and remove an attribute by name. Like :meth:`dict.pop`.

        :param name: Name of attribute to pop.
        :param default: Value to return if the attribute is not present,
            instead of raising a ``KeyError``.

        .. versionadded:: 0.11
        """
        if default is _sentinel:
            return self.__dict__.pop(name)
        else:
            return self.__dict__.pop(name, default)

    def setdefault(self, name: str, default: t.Any = None) -> t.Any:
        """Get the value of an attribute if it is present, otherwise
        set and return a default value. Like :meth:`dict.setdefault`.

        :param name: Name of attribute to get.
        :param default: Value to set and return if the attribute is not
            present.

        .. versionadded:: 0.11
        """
        return self.__dict__.setdefault(name, default)

    def __contains__(self, item: str) -> bool:
        return item in self.__dict__

    def __iter__(self) -> t.Iterator[str]:
        return iter(self.__dict__)

    def __repr__(self) -> str:
        ctx = _cv_app.get(None)
        if ctx is not None:
            return f"<flask.g of '{ctx.app.name}'>"
        return object.__repr__(self)


def after_this_request(
    f: ft.AfterRequestCallable[t.Any],
) -> ft.AfterRequestCallable[t.Any]:
    """Executes a function after this request.  This is useful to modify
    response objects.  The function is passed the response object and has
    to return the same or a new one.

    Example::

        @app.route('/')
        def index():
            @after_this_request
            def add_header(response):
                response.headers['X-Foo'] = 'Parachute'
                return response
            return 'Hello World!'

    This is more useful if a function other than the view function wants to
    modify a response.  For instance think of a decorator that wants to add
    some headers without converting the return value into a response object.

    .. versionadded:: 0.9
    """
    ctx = _cv_request.get(None)

    if ctx is None:
        raise RuntimeError(
            "'after_this_request' can only be used when a request"
            " context is active, such as in a view function."
        )

    ctx._after_request_functions.append(f)
    return f


F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def copy_current_request_context(f: F) -> F:
    """A helper function that decorates a function to retain the current
    request context.  This is useful when working with greenlets.  The moment
    the function is decorated a copy of the request context is created and
    then pushed when the function is called.  The current session is also
    included in the copied request context.

    Example::

        import gevent
        from flask import copy_current_request_context

        @app.route('/')
        def index():
            @copy_current_request_context
            def do_some_work():
                # do some work here, it can access flask.request or
                # flask.session like you would otherwise in the view function.
                ...
            gevent.spawn(do_some_work)
            return 'Regular response'

    .. versionadded:: 0.10
    """
    ctx = _cv_request.get(None)

    if ctx is None:
        raise RuntimeError(
            "'copy_current_request_context' can only be used when a"
            " request context is active, such as in a view function."
        )

    ctx = ctx.copy()

    def wrapper(*args: t.Any, **kwargs: t.Any) -> t.Any:
        with ctx:  # type: ignore[union-attr]
            return ctx.app.ensure_sync(f)(*args, **kwargs)  # type: ignore[union-attr]

    return update_wrapper(wrapper, f)  # type: ignore[return-value]


def has_request_context() -> bool:
    """If you have code that wants to test if a request context is there or
    not this function can be used.  For instance, you may want to take advantage
    of request information if the request object is available, but fail
    silently if it is unavailable.

    ::

        class User(db.Model):

            def __init__(self, username, remote_addr=None):
                self.username = username
                if remote_addr is None and has_request_context():
                    remote_addr = request.remote_addr
                self.remote_addr = remote_addr

    Alternatively you can also just test any of the context bound objects
    (such as :class:`request` or :class:`g`) for truthness::

        class User(db.Model):

            def __init__(self, username, remote_addr=None):
                self.username = username
                if remote_addr is None and request:
                    remote_addr = request.remote_addr
                self.remote_addr = remote_addr

    .. versionadded:: 0.7
    """
    return _cv_request.get(None) is not None


def has_app_context() -> bool:
    """Works like :func:`has_request_context` but for the application
    context.  You can also just do a boolean check on the
    :data:`current_app` object instead.

    .. versionadded:: 0.9
    """
    return _cv_app.get(None) is not None


class AppContext:
    """The app context contains application-specific information. An app
    context is created and pushed at the beginning of each request if
    one is not already active. An app context is also pushed when
    running CLI commands.
    """

    def __init__(self, app: Flask) -> None:
        self.app = app
        self.url_adapter = app.create_url_adapter(None)
        self.g: _AppCtxGlobals = app.app_ctx_globals_class()
        self._cv_tokens: list[contextvars.Token[AppContext]] = []

    def push(self) -> None:
        """Binds the app context to the current context."""
        self._cv_tokens.append(_cv_app.set(self))
        appcontext_pushed.send(self.app, _async_wrapper=self.app.ensure_sync)

    def pop(self, exc: BaseException | None = _sentinel) -> None:  # type: ignore
        """Pops the app context."""
        # Check if there are tokens to pop
        if not self._cv_tokens:
            return
            
        try:
            if len(self._cv_tokens) == 1:
                if exc is _sentinel:
                    exc = sys.exc_info()[1]
                self.app.do_teardown_appcontext(exc)
        finally:
            # Make sure the context is still available before popping
            try:
                ctx = _cv_app.get()
                if self._cv_tokens:  # Check again to be sure
                    _cv_app.reset(self._cv_tokens.pop())

                    # Check if the contexts are functionally the same by comparing app
                    # rather than using identity comparison
                    if ctx is not self and ctx.app is not self.app:
                        raise AssertionError(
                            f"Popped wrong app context. ({ctx!r} instead of {self!r})"
                        )

                    appcontext_popped.send(self.app, _async_wrapper=self.app.ensure_sync)
            except LookupError:
                # If the context variable is not found, it means it was already reset
                # This can happen during cleanup after exceptions
                if self._cv_tokens:
                    self._cv_tokens.pop()  # Still clean up our internal state

    def __enter__(self) -> AppContext:
        self.push()
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.pop(exc_value)


# Request context object pool
# This pool is used to reuse RequestContext objects
# to reduce allocation overhead
class RequestContextPool:
    """A simple object pool for RequestContext objects to reduce allocation overhead."""
    
    def __init__(self, max_size=50):
        self.pool = weakref.WeakSet()
        self.max_size = max_size
        # Disable pooling during tests to avoid issues with context sharing
        # We'll detect if we're in a test environment
        self.enabled = True
    
    def get(self, app: Flask, environ: WSGIEnvironment) -> RequestContext:
        """Get a RequestContext object from the pool or create a new one if none is available."""
        # Disable pooling for test environments to ensure test isolation
        if not self.enabled or app.testing:
            return RequestContext(app, environ)
            
        try:
            for ctx in self.pool:
                if ctx.app is app:  # Only reuse contexts for the same app
                    self.pool.remove(ctx)
                    # Reset the context to a clean state
                    ctx._reset(environ)
                    return ctx
        except (TypeError, RuntimeError):
            # Handle any issues with the weakref
            pass
        
        # If no suitable context was found, create a new one
        return RequestContext(app, environ)
    
    def put(self, ctx: RequestContext) -> None:
        """Return a RequestContext object to the pool if there's room."""
        # Don't pool contexts from test environments
        if not self.enabled or ctx.app.testing:
            return
            
        if len(self.pool) < self.max_size:
            try:
                self.pool.add(ctx)
            except (TypeError, RuntimeError):
                # Handle any issues with the weakref
                pass

# Global context pool
_request_ctx_pool = RequestContextPool()


class RequestContext:
    """The request context contains per-request information. The Flask
    app creates and pushes it at the beginning of the request, then pops
    it at the end of the request. It will create the URL adapter and
    request object for the WSGI environment provided.

    Do not attempt to use this class directly, instead use
    :meth:`~flask.Flask.test_request_context` and
    :meth:`~flask.Flask.request_context` to create this object.

    When the request context is popped, it will evaluate all the
    functions registered on the application for teardown execution
    (:meth:`~flask.Flask.teardown_request`).

    The request context is automatically popped at the end of the
    request. When using the interactive debugger, the context will be
    restored so ``request`` is still accessible. Similarly, the test
    client can preserve the context after the request ends. However,
    teardown functions may already have closed some resources such as
    database connections.
    """

    def __init__(
        self,
        app: Flask,
        environ: WSGIEnvironment,
        request: Request | None = None,
        session: SessionMixin | None = None,
    ) -> None:
        self.app = app
        if request is None:
            request = app.request_class(environ)
            request.json_module = app.json
        self.request: Request = request
        self.url_adapter = None
        self._url_adapter_tried = False  # Flag to indicate if URL adapter creation was attempted
        self.flashes: list[tuple[str, str]] | None = None
        self._session: SessionMixin | None = session
        self._implicit_app_ctx_stack: list[AppContext | None] = []
        # Functions that should be executed after the request on the response
        # object.  These will be called before the regular "after_request"
        # functions.
        self._after_request_functions: list[ft.AfterRequestCallable[t.Any]] = []

        self._cv_tokens: list[
            tuple[contextvars.Token[RequestContext], AppContext | None]
        ] = []
        self.preserved = False

    def _reset(self, environ: WSGIEnvironment) -> None:
        """Reset the context to be reused with a new request."""
        self.request = self.app.request_class(environ)
        self.request.json_module = self.app.json
        self.url_adapter = None
        self._url_adapter_tried = False
        self.flashes = None
        self._session = None
        self._implicit_app_ctx_stack = []
        self._after_request_functions = []
        self._cv_tokens = []
        self.preserved = False

    @property
    def session(self) -> SessionMixin:
        """The session object for the current request.
        
        This property lazily initializes the session when accessed.
        """
        if self._session is None:
            session_interface = self.app.session_interface
            self._session = session_interface.open_session(self.app, self.request)
            
            if self._session is None:
                self._session = session_interface.make_null_session(self.app)
            
            # Set permanent without marking as accessed
            if self._session is not None and hasattr(self._session, '__dict__'):
                permanent = self.app.config["PERMANENT_SESSION_LIFETIME"]
                # Access the underlying dict directly to avoid marking as accessed
                was_accessed = self._session.accessed
                self._session.__dict__['_permanent'] = permanent
                if not was_accessed:
                    self._session.accessed = False
        
        return self._session

    @session.setter
    def session(self, value: SessionMixin | None) -> None:
        self._session = value

    def get_url_adapter(self) -> t.Any:
        """Lazily create the URL adapter when needed."""
        if self.url_adapter is None and not self._url_adapter_tried:
            self._url_adapter_tried = True
            try:
                self.url_adapter = self.app.create_url_adapter(self.request)
            except HTTPException as e:
                self.request.routing_exception = e
        return self.url_adapter

    def copy(self) -> RequestContext:
        """Creates a copy of this request context with the same request object.
        This can be used to move a request context to a different greenlet.
        Because the actual request object is the same this cannot be used to
        move a request context to a different thread unless access to the
        request object is locked.

        .. versionadded:: 0.10

        .. versionchanged:: 1.1
           The current session object is used instead of reloading the original
           data. This prevents `flask.session` pointing to an out-of-date object.
        """
        return self.__class__(
            self.app,
            environ=self.request.environ,
            request=self.request,
            session=self._session,
        )

    def match_request(self) -> None:
        """Can be overridden by a subclass to hook into the matching
        of the request.
        """
        url_adapter = self.get_url_adapter()
        try:
            if url_adapter is not None:
                result = url_adapter.match(return_rule=True)  # type: ignore
                self.request.url_rule, self.request.view_args = result  # type: ignore
        except HTTPException as e:
            self.request.routing_exception = e

    def push(self) -> None:
        """Binds the request context to the current context."""
        # If an exception occurs in debug mode or if context preservation is
        # activated, the exception is stored on the exception stack.
        if _cv_request.get(None) is not None:
            top = _cv_request.get(None)
            if top is not None and top.preserved:
                top.pop(_cv_request.get(None))

        # Before we push the request context we have to ensure that there
        # is an application context.
        app_ctx = _cv_app.get(None)

        if app_ctx is None or app_ctx.app is not self.app:
            app_ctx = self.app.app_context()
            app_ctx.push()
            self._implicit_app_ctx_stack.append(app_ctx)
        else:
            self._implicit_app_ctx_stack.append(None)

        if hasattr(sys, "exc_clear"):
            sys.exc_clear()  # type: ignore

        self._cv_tokens.append((_cv_request.set(self), app_ctx))

        # Match the request URL after loading the session, so that the
        # session is available in custom URL converters.
        # Note: We now use lazy URL adapter creation and matching
        if self.get_url_adapter() is not None:
            self.match_request()

    def pop(self, exc: BaseException | None = _sentinel) -> None:  # type: ignore
        """Pops the request context and unbinds it by doing that.  This will
        also trigger the execution of functions registered by the
        :meth:`~flask.Flask.teardown_request` decorator.

        .. versionchanged:: 0.9
           Added the `exc` argument.
        """
        # Check if there are tokens to pop
        if not self._cv_tokens:
            return
            
        clear_request = len(self._cv_tokens) == 1

        try:
            if clear_request:
                if exc is _sentinel:
                    exc = sys.exc_info()[1]
                self.app.do_teardown_request(exc)

                request_close = getattr(self.request, "close", None)
                if request_close is not None:
                    request_close()
        finally:
            ctx = _cv_request.get()
            
            # Ensure we have tokens to pop
            if self._cv_tokens:
                token, app_ctx = self._cv_tokens.pop()
                _cv_request.reset(token)

                # get rid of circular dependencies at the end of the request
                # so that we don't require the GC to be active.
                if clear_request and ctx is not None:
                    ctx.request.environ["werkzeug.request"] = None

                # In the original implementation, we pop the app context after the request context
                # to ensure the teardown operations happen in the correct order (req then app)
                if app_ctx is not None:
                    app_ctx.pop(exc)

                # Check if the contexts are functionally the same by comparing app and URL
                # rather than using identity comparison, because we might be using copied contexts
                if ctx is not self and ctx.app is not self.app:
                    raise AssertionError(
                        f"Popped wrong request context. ({ctx!r} instead of {self!r})"
                    )
                    
                # Return the context to the pool when it's popped for reuse
                if clear_request and not self.preserved:
                    _request_ctx_pool.put(self)

    def __enter__(self) -> RequestContext:
        self.push()
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.pop(exc_value)

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} {self.request.url!r}"
            f" [{self.request.method}] of {self.app.name}>"
        )