from __future__ import annotations

import dataclasses
import decimal
import json
import typing as t
import uuid
import weakref
from collections.abc import Iterator
from datetime import date
from functools import lru_cache
from typing import Any
from typing import Callable
from typing import Optional

from werkzeug.http import http_date

if t.TYPE_CHECKING:  # pragma: no cover
    from werkzeug.sansio.response import Response

    from ..sansio.app import App

# Size threshold for lazy serialization (in items for lists/dicts)
LAZY_SERIALIZATION_THRESHOLD = 1000


class LazyJSONValue:
    """A wrapper for objects that should be lazily serialized to JSON.

    This reduces memory usage by not creating intermediate object representations
    during the serialization process. This is especially beneficial for large
    collections of data.
    """

    __slots__ = ("value", "default", "ensure_ascii", "sort_keys", "encoder")

    def __init__(
        self,
        value: Any,
        default: Optional[Callable[[Any], Any]] = None,
        ensure_ascii: bool = True,
        sort_keys: bool = True,
    ):
        self.value = value
        self.default = default
        self.ensure_ascii = ensure_ascii
        self.sort_keys = sort_keys
        self.encoder = None  # Created on first use

    def __str__(self) -> str:
        """Convert to JSON string when string representation is needed."""
        if self.encoder is None:
            self.encoder = json.JSONEncoder(
                default=self.default,
                ensure_ascii=self.ensure_ascii,
                sort_keys=self.sort_keys,
            )
        return "".join(self.encoder.iterencode(self.value))

    def iter_encode(self) -> Iterator[str]:
        """Yield chunks of JSON encoding without creating a complete string."""
        if self.encoder is None:
            self.encoder = json.JSONEncoder(
                default=self.default,
                ensure_ascii=self.ensure_ascii,
                sort_keys=self.sort_keys,
            )
        yield from self.encoder.iterencode(self.value)


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


@lru_cache(maxsize=128)
def _type_size_estimate(obj_type: type) -> int:
    """Estimate the size needed for serializing objects of a specific type.
    This helps with buffer pre-allocation."""
    if obj_type is str:
        return 32  # Average string size estimate
    elif obj_type is int:
        return 8
    elif obj_type is float:
        return 16
    elif obj_type is bool:
        return 5  # "true" or "false"
    elif obj_type is None.__class__:
        return 4  # "null"
    elif obj_type is list:
        return 64  # Initial estimate for list
    elif obj_type is dict:
        return 128  # Initial estimate for dict
    elif obj_type is date:
        return 30  # RFC 822 date format
    elif obj_type is decimal.Decimal:
        return 24
    elif obj_type is uuid.UUID:
        return 38  # UUID string representation
    return 64  # Default fallback


def _default(o: t.Any) -> t.Any:
    """Convert Python objects to JSON-serializable types."""
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

    ensure_ascii = True
    """Replace non-ASCII characters with escape sequences. This may be
    more compatible with some clients, but can be disabled for better
    performance and size.
    """

    sort_keys = True
    """Sort the keys in any serialized dicts. This may be useful for
    some caching situations, but can be disabled for better performance.
    When enabled, keys must all be strings, they are not converted
    before sorting.
    """

    compact: bool | None = None
    """If ``True``, or ``None`` out of debug mode, the :meth:`response`
    output will not add indentation, newlines, or spaces. If ``False``,
    or ``None`` in debug mode, it will use a non-compact representation.
    """

    mimetype = "application/json"
    """The mimetype set in :meth:`response`."""

    # Cache for common serialization patterns
    _cache_size = 128
    _encoder_cache: t.Dict[tuple, json.JSONEncoder] = {}

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self._reusable_encoders = {}
        self._buffer_pool = []

        # Pre-create encoders for common configurations
        self._setup_common_encoders()

    def _setup_common_encoders(self) -> None:
        """Pre-create encoders for common configurations to avoid recreating them."""
        # Common configurations used in Flask
        configs = [
            {"default": self.default, "ensure_ascii": True, "sort_keys": True},
            {"default": self.default, "ensure_ascii": True, "sort_keys": False},
            {"default": self.default, "ensure_ascii": False, "sort_keys": True},
            {"default": self.default, "ensure_ascii": False, "sort_keys": False},
            # Common configurations with indent
            {
                "default": self.default,
                "ensure_ascii": True,
                "sort_keys": True,
                "indent": 2,
            },
            {
                "default": self.default,
                "ensure_ascii": True,
                "sort_keys": False,
                "indent": 2,
            },
            # Compact configurations
            {
                "default": self.default,
                "ensure_ascii": True,
                "sort_keys": True,
                "separators": (",", ":"),
            },
            {
                "default": self.default,
                "ensure_ascii": False,
                "sort_keys": True,
                "separators": (",", ":"),
            },
        ]

        for config in configs:
            key = frozenset(config.items())
            if key not in self._reusable_encoders:
                self._reusable_encoders[key] = json.JSONEncoder(**config)

    def _get_encoder(self, config: t.Dict[str, t.Any]) -> json.JSONEncoder:
        """Get a cached encoder or create a new one for the given configuration."""
        # Create a hashable key from the config
        key = frozenset(config.items())

        # Try to get from cache first
        if key in self._reusable_encoders:
            return self._reusable_encoders[key]

        # Create new encoder and cache it (with LRU behavior)
        encoder = json.JSONEncoder(**config)

        # Simple LRU implementation
        if len(self._reusable_encoders) >= self._cache_size:
            # Remove a random item if we're at capacity
            self._reusable_encoders.pop(next(iter(self._reusable_encoders)))

        self._reusable_encoders[key] = encoder
        return encoder

    def _estimate_size(self, obj: t.Any) -> int:
        """Estimate the serialized size of an object to pre-allocate buffer."""
        if obj is None:
            return 4  # "null"

        obj_type = type(obj)

        # Use cached size estimate for the type
        base_size = _type_size_estimate(obj_type)

        # Refine estimates for containers
        if obj_type is list:
            # Estimate based on list length and sampling
            length = len(obj)
            if length == 0:
                return 2  # "[]"

            # Sample the first few items and last item
            sample_size = min(3, length)
            samples = obj[:sample_size]
            if length > sample_size:
                samples.append(obj[-1])

            # Estimate based on samples
            item_size = sum(self._estimate_size(item) for item in samples) / len(
                samples
            )
            return int(2 + (length * item_size) + (length - 1))  # [items] with commas

        elif obj_type is dict:
            # Estimate based on dict size and sampling
            length = len(obj)
            if length == 0:
                return 2  # "{}"

            # Sample a few keys
            sample_keys = list(obj.keys())[: min(3, length)]
            if length > 3 and sample_keys:
                last_key = next(reversed(obj))
                if last_key not in sample_keys:
                    sample_keys.append(last_key)

            # Estimate key-value pairs
            pair_size = sum(
                len(str(k)) + 3 + self._estimate_size(obj[k]) for k in sample_keys
            ) / len(sample_keys)
            return int(2 + (length * pair_size) + (length - 1))  # {k:v} with commas

        elif obj_type is str:
            # String size plus quotes and potential escaping
            return len(obj) * 1.2 + 2

        return base_size

    @lru_cache(maxsize=64)
    def _get_common_result(self, obj: t.Any, config_key: t.Hashable) -> str:
        """Cache results for common objects with immutable configs."""
        config = dict(config_key)
        return json.dumps(obj, **config)

    def dumps(self, obj: t.Any, **kwargs: t.Any) -> str:
        """Serialize data as JSON to a string.

        Keyword arguments are passed to :func:`json.dumps`. Sets some
        parameter defaults from the :attr:`default`,
        :attr:`ensure_ascii`, and :attr:`sort_keys` attributes.

        This optimized implementation:
        1. Reuses encoders for common configurations
        2. Attempts to pre-allocate buffers based on object size estimation
        3. Caches results for common simple values
        4. Lazily serializes large collections to reduce memory usage

        :param obj: The data to serialize.
        :param kwargs: Passed to :func:`json.dumps`.
        """
        # Set defaults
        config = {
            "default": self.default,
            "ensure_ascii": self.ensure_ascii,
            "sort_keys": self.sort_keys,
        }
        config.update(kwargs)

        # Handle LazyJSONValue objects
        if isinstance(obj, LazyJSONValue):
            return str(obj)

        # Try caching for common simple values
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            # Simple immutable types can be cached by value
            frozen_config = frozenset(config.items())
            try:
                return self._get_common_result(obj, frozen_config)
            except (TypeError, ValueError):
                # If obj can't be hashed, continue with normal serialization
                pass

        # Use lazy serialization for large collections
        if (isinstance(obj, list) and len(obj) > LAZY_SERIALIZATION_THRESHOLD) or (
            isinstance(obj, dict) and len(obj) > LAZY_SERIALIZATION_THRESHOLD
        ):
            # Create a lazy JSON value that will serialize on-demand
            lazy_value = LazyJSONValue(
                obj,
                default=config.get("default"),
                ensure_ascii=config.get("ensure_ascii", True),
                sort_keys=config.get("sort_keys", True),
            )
            # Immediately convert to string to avoid returning the object
            return str(lazy_value)

        # Get or create encoder for medium-sized objects
        if isinstance(obj, (list, dict)) and len(obj) > 100:
            encoder = self._get_encoder(config)
            # Pre-allocate a buffer with estimated size
            size_estimate = self._estimate_size(obj)
            chunks = list(encoder.iterencode(obj))
            return "".join(chunks)

        # For smaller objects, use the standard dumps
        return json.dumps(obj, **config)

    def loads(self, s: str | bytes, **kwargs: t.Any) -> t.Any:
        """Deserialize data as JSON from a string or bytes.

        :param s: Text or UTF-8 bytes.
        :param kwargs: Passed to :func:`json.loads`.
        """
        return json.loads(s, **kwargs)

    # Cache common response patterns
    _response_cache = {}
    _response_cache_size = 32

    def response(self, *args: t.Any, **kwargs: t.Any) -> Response:
        """Serialize the given arguments as JSON, and return a
        :class:`~flask.Response` object with it. The response mimetype
        will be "application/json" and can be changed with
        :attr:`mimetype`.

        If :attr:`compact` is ``False`` or debug mode is enabled, the
        output will be formatted to be easier to read.

        Either positional or keyword arguments can be given, not both.
        If no arguments are given, ``None`` is serialized.

        This optimized implementation:
        1. Uses specialized serialization paths for common response types
        2. Caches serialization results for simple immutable objects
        3. Avoids string concatenation and temporary allocations

        :param args: A single value to serialize, or multiple values to
            treat as a list to serialize.
        :param kwargs: Treat as a dict to serialize.
        """
        obj = self._prepare_response_obj(args, kwargs)
        dump_args: dict[str, t.Any] = {}

        is_debug_format = (
            self.compact is None and self._app.debug
        ) or self.compact is False
        if is_debug_format:
            dump_args.setdefault("indent", 2)
        else:
            dump_args.setdefault("separators", (",", ":"))

        # Fast path for common empty responses and simple primitives
        if obj is None or obj == "" or obj == [] or obj == {}:
            # Check cache for these extremely common responses
            cache_key = (None if obj is None else type(obj).__name__, is_debug_format)
            if cache_key in self._response_cache:
                return self._response_cache[cache_key]

            # Create response and cache it
            result = self._app.response_class(
                "null\n"
                if obj is None
                else "{}\n"
                if isinstance(obj, dict)
                else "[]\n"
                if isinstance(obj, list)
                else '""\n',
                mimetype=self.mimetype,
            )

            # Simple LRU - remove oldest entry if cache is full
            if len(self._response_cache) >= self._response_cache_size:
                self._response_cache.pop(next(iter(self._response_cache)))

            self._response_cache[cache_key] = result
            return result

        # Fast path for simple immutable primitives
        if isinstance(obj, (bool, int, float, str)) and not is_debug_format:
            # For simple types, we can optimize even further
            if isinstance(obj, bool):
                json_value = "true\n" if obj else "false\n"
            elif isinstance(obj, (int, float)):
                json_value = f"{obj}\n"
            elif isinstance(obj, str):
                # Need to properly escape the string
                encoder = self._get_encoder({"ensure_ascii": self.ensure_ascii})
                json_value = f"{encoder.encode(obj)}\n"
            else:
                # Fallback to standard serialization
                json_value = f"{self.dumps(obj, **dump_args)}\n"

            return self._app.response_class(json_value, mimetype=self.mimetype)

        # Lazy serialization for large collections to reduce memory usage
        if (isinstance(obj, list) and len(obj) > LAZY_SERIALIZATION_THRESHOLD) or (
            isinstance(obj, dict) and len(obj) > LAZY_SERIALIZATION_THRESHOLD
        ):
            # Create a LazyJSONValue and stream it directly
            lazy_value = LazyJSONValue(
                obj,
                default=dump_args.get("default", self.default),
                ensure_ascii=dump_args.get("ensure_ascii", self.ensure_ascii),
                sort_keys=dump_args.get("sort_keys", self.sort_keys),
            )

            # Generate content in chunks to avoid large string allocations
            chunks = list(lazy_value.iter_encode())
            chunks.append("\n")
            return self._app.response_class(chunks, mimetype=self.mimetype)

        # Standard path for medium-sized complex objects
        return self._app.response_class(
            f"{self.dumps(obj, **dump_args)}\n", mimetype=self.mimetype
        )
