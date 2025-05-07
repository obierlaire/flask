from __future__ import annotations

import dataclasses
import decimal
import json
import typing as t
import uuid
import weakref
from datetime import date

from werkzeug.http import http_date

if t.TYPE_CHECKING:  # pragma: no cover
    from werkzeug.sansio.response import Response

    from ..sansio.app import App


class JSONProvider:
    """A standard set of JSON operations for an application. Subclasses
    of this can be used to customize JSON behavior or use different
    JSON libraries.

    To implement a provider for a specific library, subclass this base
    class and implement at least :meth:`dumps` and :meth:`loads`. All
    other methods have default implementations.

    To use a different provider, either subclass ``Flask`` and set
    :attr:`~flask.Flask.json_provider_class` to a provider class, or set
    :attr:`app.json <flask.Flask.json>` to an instance of the class.

    :param app: An application instance. This will be stored as a
        :class:`weakref.proxy` on the :attr:`_app` attribute.

    .. versionadded:: 2.2
    """

    def __init__(self, app: App) -> None:
        self._app: App = weakref.proxy(app)

    def dumps(self, obj: t.Any, **kwargs: t.Any) -> str:
        """Serialize data as JSON.

        :param obj: The data to serialize.
        :param kwargs: May be passed to the underlying JSON library.
        """
        raise NotImplementedError

    def dump(self, obj: t.Any, fp: t.IO[str], **kwargs: t.Any) -> None:
        """Serialize data as JSON and write to a file.

        :param obj: The data to serialize.
        :param fp: A file opened for writing text. Should use the UTF-8
            encoding to be valid JSON.
        :param kwargs: May be passed to the underlying JSON library.
        """
        fp.write(self.dumps(obj, **kwargs))

    def loads(self, s: str | bytes, **kwargs: t.Any) -> t.Any:
        """Deserialize data as JSON.

        :param s: Text or UTF-8 bytes.
        :param kwargs: May be passed to the underlying JSON library.
        """
        raise NotImplementedError

    def load(self, fp: t.IO[t.AnyStr], **kwargs: t.Any) -> t.Any:
        """Deserialize data as JSON read from a file.

        :param fp: A file opened for reading text or UTF-8 bytes.
        :param kwargs: May be passed to the underlying JSON library.
        """
        return self.loads(fp.read(), **kwargs)

    def _prepare_response_obj(
        self, args: tuple[t.Any, ...], kwargs: dict[str, t.Any]
    ) -> t.Any:
        if args and kwargs:
            raise TypeError("app.json.response() takes either args or kwargs, not both")

        if not args and not kwargs:
            return None

        if len(args) == 1:
            return args[0]

        return args or kwargs

    def response(self, *args: t.Any, **kwargs: t.Any) -> Response:
        """Serialize the given arguments as JSON, and return a
        :class:`~flask.Response` object with the ``application/json``
        mimetype.

        The :func:`~flask.json.jsonify` function calls this method for
        the current application.

        Either positional or keyword arguments can be given, not both.
        If no arguments are given, ``None`` is serialized.

        :param args: A single value to serialize, or multiple values to
            treat as a list to serialize.
        :param kwargs: Treat as a dict to serialize.
        """
        obj = self._prepare_response_obj(args, kwargs)
        return self._app.response_class(self.dumps(obj), mimetype="application/json")


def _default(o: t.Any) -> t.Any:
    if isinstance(o, date):
        return http_date(o)

    if isinstance(o, (decimal.Decimal, uuid.UUID)):
        return str(o)

    if dataclasses and dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)  # type: ignore[arg-type]

    if hasattr(o, "__html__"):
        return str(o.__html__())

    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


class DefaultJSONProvider(JSONProvider):
    """Provide JSON operations using Python's built-in :mod:`json`
    library. Serializes the following additional data types:

    -   :class:`datetime.datetime` and :class:`datetime.date` are
        serialized to :rfc:`822` strings. This is the same as the HTTP
        date format.
    -   :class:`uuid.UUID` is serialized to a string.
    -   :class:`dataclasses.dataclass` is passed to
        :func:`dataclasses.asdict`.
    -   :class:`~markupsafe.Markup` (or any object with a ``__html__``
        method) will call the ``__html__`` method to get a string.
    """

    default: t.Callable[[t.Any], t.Any] = staticmethod(_default)  # type: ignore[assignment]
    """Apply this function to any object that :meth:`json.dumps` does
    not know how to serialize. It should return a valid JSON type or
    raise a ``TypeError``.
    """

    ensure_ascii = False
    """Replace non-ASCII characters with escape sequences. This may be
    more compatible with some clients, but is disabled by default for better
    performance and smaller payload size. 
    
    This can be configured via the JSON_ENSURE_ASCII config setting.
    For security reasons, when rendering JSON directly into HTML (e.g., using
    the tojson filter in templates), ASCII escaping is automatically enabled
    for strings containing HTML tags like "</script>".
    """

    _sort_keys = False
    """Internal sort_keys storage, use the sort_keys property instead"""
    
    @property
    def sort_keys(self) -> bool:
        """Sort the keys in any serialized dicts. This may be useful for
        some caching situations, but is disabled by default for better performance.
        In debug mode, this is automatically set to True for reproducible outputs
        unless explicitly configured otherwise via the JSON_SORT_KEYS config.
        When enabled, keys must all be strings, they are not converted
        before sorting.
        """
        if hasattr(self._app, 'config'):
            # For backward compatibility, debug mode should force sort_keys=True
            # unless explicitly configured otherwise via JSON_SORT_KEYS
            if 'JSON_SORT_KEYS' in self._app.config:
                return self._app.config['JSON_SORT_KEYS']
                
            # Check current debug status directly using app.debug attribute
            # rather than using config, to catch changes after initialization
            if hasattr(self._app, 'debug') and self._app.debug:
                return True
        
        return self._sort_keys
        
    @sort_keys.setter
    def sort_keys(self, value: bool) -> None:
        self._sort_keys = value

    compact: bool | None = None
    """If ``True``, or ``None`` out of debug mode, the :meth:`response`
    output will not add indentation, newlines, or spaces. If ``False``,
    or ``None`` in debug mode, it will use a non-compact representation.
    """

    mimetype = "application/json"
    """The mimetype set in :meth:`response`."""

    def __init__(self, app: 'App') -> None:
        super().__init__(app)
        self._cache = self._get_cache()
        
        # Get config settings for JSON serialization if available
        if hasattr(app, 'config'):
            self.ensure_ascii = app.config.get('JSON_ENSURE_ASCII', self.ensure_ascii)
            
            # For backward compatibility, in debug mode sort_keys should be True 
            # unless explicitly configured otherwise - handled by the property
            if 'JSON_SORT_KEYS' in app.config:
                self._sort_keys = app.config.get('JSON_SORT_KEYS')

    def _get_cache(self) -> t.Dict[t.Tuple[int, t.Tuple, frozenset], str]:
        """Create a simple cache for JSON serialization.
        
        Returns a dict that will store serialized JSON strings, 
        with the limit of storing the 128 most recently used values.
        """
        try:
            from functools import lru_cache
        except ImportError:
            # Simple dict for older Python versions
            return {}
        
        # Using a dict subclass with an lru_cache for __getitem__
        # This avoids overhead of a full LRU cache implementation
        class LRUDict(dict):
            @lru_cache(maxsize=128)
            def __getitem__(self, key):
                return super().__getitem__(key)
                
        return LRUDict()

    def dumps(self, obj: t.Any, **kwargs: t.Any) -> str:
        """Serialize data as JSON to a string.

        Keyword arguments are passed to :func:`json.dumps`. Sets some
        parameter defaults from the :attr:`default`,
        :attr:`ensure_ascii`, and :attr:`sort_keys` attributes.
        
        Uses an internal cache for repeated serializations of the same object
        with the same parameters.
        
        For security reasons, ensure_ascii is automatically enabled for strings
        containing HTML tags like "</script>" to prevent XSS when used in HTML
        contexts.

        :param obj: The data to serialize.
        :param kwargs: Passed to :func:`json.dumps`.
        """
        kwargs.setdefault("default", self.default)
        kwargs.setdefault("ensure_ascii", self.ensure_ascii)
        kwargs.setdefault("sort_keys", self.sort_keys)  # Property access
        
        # For security, ensure ASCII escaping for HTML content
        if not kwargs.get("ensure_ascii") and self._html_content_detected(obj):
            kwargs["ensure_ascii"] = True
        
        # Try to use cached result if object is hashable and no custom kwargs are used
        default_kwargs = {
            "default": self.default, 
            "ensure_ascii": kwargs["ensure_ascii"], 
            "sort_keys": kwargs["sort_keys"]  # Use value from kwargs
        }
        
        if kwargs == default_kwargs:
            try:
                obj_id = id(obj)
                hashable_params = frozenset((k, v) for k, v in kwargs.items() if isinstance(v, t.Hashable))
                cache_key = (obj_id, (), hashable_params)
                
                if cache_key in self._cache:
                    return self._cache[cache_key]
                
                result = json.dumps(obj, **kwargs)
                self._cache[cache_key] = result
                return result
            except (TypeError, ValueError):
                # Object is not hashable or another error occurred, proceed without caching
                pass
                
        return json.dumps(obj, **kwargs)

    def loads(self, s: str | bytes, **kwargs: t.Any) -> t.Any:
        """Deserialize data as JSON from a string or bytes.

        :param s: Text or UTF-8 bytes.
        :param kwargs: Passed to :func:`json.loads`.
        """
        return json.loads(s, **kwargs)
        
    def dump(self, obj: t.Any, fp: t.IO[str], **kwargs: t.Any) -> None:
        """Serialize data as JSON and write to a file using direct streaming.
        
        Instead of first serializing to a string with dumps() and then writing to 
        the file, this method uses json.dump() directly for better performance and 
        memory efficiency, especially with large payloads.

        :param obj: The data to serialize.
        :param fp: A file opened for writing text. Should use the UTF-8
            encoding to be valid JSON.
        :param kwargs: May be passed to the underlying JSON library.
        """
        kwargs.setdefault("default", self.default)
        kwargs.setdefault("ensure_ascii", self.ensure_ascii)
        kwargs.setdefault("sort_keys", self.sort_keys)  # Property access
        
        # For security, ensure ASCII escaping for HTML content
        if not kwargs.get("ensure_ascii") and self._html_content_detected(obj):
            kwargs["ensure_ascii"] = True
            
        json.dump(obj, fp, **kwargs)

    def _html_content_detected(self, obj: t.Any) -> bool:
        """Check if the object contains any HTML-sensitive content like </script> tags.
        This is used to automatically enable ensure_ascii for security when needed.
        """
        if obj is None:
            return False
            
        if isinstance(obj, str) and "</script>" in obj:
            return True
            
        if isinstance(obj, dict):
            for k, v in obj.items():
                if (isinstance(k, str) and "</script>" in k) or self._html_content_detected(v):
                    return True
                    
        if isinstance(obj, (list, tuple)):
            for item in obj:
                if self._html_content_detected(item):
                    return True
                    
        return False

    def response(self, *args: t.Any, **kwargs: t.Any) -> Response:
        """Serialize the given arguments as JSON, and return a
        :class:`~flask.Response` object with it. The response mimetype
        will be "application/json" and can be changed with
        :attr:`mimetype`.

        If :attr:`compact` is ``False`` or debug mode is enabled, the
        output will be formatted to be easier to read.

        Either positional or keyword arguments can be given, not both.
        If no arguments are given, ``None`` is serialized.

        :param args: A single value to serialize, or multiple values to
            treat as a list to serialize.
        :param kwargs: Treat as a dict to serialize.
        """
        obj = self._prepare_response_obj(args, kwargs)
        dump_args: dict[str, t.Any] = {}

        if (self.compact is None and self._app.debug) or self.compact is False:
            dump_args.setdefault("indent", 2)
        else:
            dump_args.setdefault("separators", (",", ":"))

        # For responses that might be included in HTML (e.g., in a <script> tag),
        # we need to ensure </script> is escaped to prevent XSS.
        # Force ensure_ascii for Jinja context or if HTML content might be present.
        if self._html_content_detected(obj):
            dump_args["ensure_ascii"] = True

        return self._app.response_class(
            f"{self.dumps(obj, **dump_args)}\n", mimetype=self.mimetype
        )
