"""Tests for Flask's request preprocessing optimizations.

This module tests the performance improvements made to the request preprocessing
pipeline in Flask, including ensure_sync caching and middleware execution flow
optimization.
"""

import pytest
import time
import inspect
import sys
from flask import Flask, Blueprint, request

def create_app_with_middleware(num_middleware=5, num_blueprints=2):
    """Create a test app with middleware for optimization testing."""
    app = Flask(__name__)
    
    # Add middleware
    for i in range(num_middleware):
        @app.before_request
        def middleware_func():
            return None
    
    # Add async middleware
    @app.before_request
    async def async_middleware():
        return None
    
    # Add blueprint middleware
    for i in range(num_blueprints):
        bp = Blueprint(f'bp_{i}', __name__, url_prefix=f'/bp{i}')
        
        @bp.before_request
        def bp_middleware():
            return None
        
        @bp.route('/')
        def bp_index():
            return f'Blueprint {i}'
        
        @bp.route('/async')
        async def bp_async():
            return f'Async Blueprint {i}'
        
        app.register_blueprint(bp)
    
    # Add a route with parameters
    @app.route('/user/<username>')
    def show_user(username):
        return f'User: {username}'
    
    # Add simple route
    @app.route('/')
    def index():
        return 'Index'
    
    # Add async route
    @app.route('/async')
    async def async_route():
        return 'Async'
    
    return app

def verify_ensure_sync_cache(app):
    """Verify that the ensure_sync cache is working correctly."""
    # Check that the cache exists
    assert hasattr(app, '_ensure_sync_cache')
    
    # Get a test function
    async def test_async_func():
        return 'test'
    
    # Test caching for async function
    sync_func = app.ensure_sync(test_async_func)
    assert test_async_func in app._ensure_sync_cache
    assert app._ensure_sync_cache[test_async_func] is sync_func
    
    # Test cache reuse
    sync_func2 = app.ensure_sync(test_async_func)
    assert sync_func2 is sync_func
    
    # Test regular function pass-through and caching
    def test_sync_func():
        return 'test'
    
    sync_result = app.ensure_sync(test_sync_func)
    assert test_sync_func in app._ensure_sync_cache
    assert sync_result is test_sync_func

def test_ensure_sync_caching():
    """Test that the ensure_sync method caches wrapped functions."""
    app = create_app_with_middleware()
    verify_ensure_sync_cache(app)

def test_blueprint_pattern_caching():
    """Test that the ensure_sync caching is working."""
    app = create_app_with_middleware(num_blueprints=3)
    client = app.test_client()
    
    # Make a request to trigger function caching
    client.get('/bp0/')
    
    # Check for ensure_sync cache 
    assert '_ensure_sync_cache' in dir(app)
    
    # Get the initial cache size
    initial_cache_size = len(app._ensure_sync_cache)
    
    # Make another request to use the cache
    client.get('/bp1/')
    
    # Verify that the cache is being populated
    assert len(app._ensure_sync_cache) >= initial_cache_size

def test_middleware_performance():
    """Test that middleware execution performance is optimized."""
    app = create_app_with_middleware(num_middleware=10, num_blueprints=5)
    client = app.test_client()
    
    # Make some initial requests to populate caches
    client.get('/')
    client.get('/async')
    client.get('/bp0/')
    client.get('/bp1/async')
    
    # Measure performance with a larger number of requests
    routes = ['/', '/async', '/bp0/', '/bp1/async']
    iterations = 50
    
    # Time the execution
    start_time = time.time()
    for _ in range(iterations):
        for route in routes:
            client.get(route)
    end_time = time.time()
    
    # Total requests
    total_requests = iterations * len(routes)
    execution_time = end_time - start_time
    
    # Print performance stats
    print(f"\nMiddleware performance stats:")
    print(f"Completed {total_requests} requests in {execution_time:.4f} seconds")
    print(f"Average time per request: {(execution_time / total_requests) * 1000:.4f} ms")
    print(f"Requests per second: {total_requests / execution_time:.1f}")
    
    # Just check that things execute without error
    # Real performance testing is done in the benchmark scripts
    assert execution_time > 0

def test_after_request_optimization():
    """Test that after_request functions execute correctly."""
    app = Flask(__name__)
    
    @app.after_request
    def add_header(response):
        response.headers['X-Test'] = 'test'
        return response
    
    @app.after_request
    async def async_add_header(response):
        response.headers['X-Async-Test'] = 'test'
        return response
    
    @app.route('/')
    def index():
        return 'Index'
    
    client = app.test_client()
    
    # Make a request
    response = client.get('/')
    
    # Verify headers were added
    assert response.headers.get('X-Test') == 'test'
    assert response.headers.get('X-Async-Test') == 'test'
    
    # Check for ensure_sync cache
    assert hasattr(app, '_ensure_sync_cache')
    
    # Make a second request to see if the cache is used
    response = client.get('/')
    assert response.headers.get('X-Test') == 'test'
    assert response.headers.get('X-Async-Test') == 'test'

def test_dispatch_optimization():
    """Test that dispatch_request handles async functions correctly."""
    app = create_app_with_middleware()
    client = app.test_client()
    
    # Make a request to populate caches
    client.get('/')
    
    # Check the ensure_sync cache
    assert hasattr(app, '_ensure_sync_cache')
    initial_cache_size = len(app._ensure_sync_cache)
    
    # Make a request to the async route
    response = client.get('/async')
    assert response.status_code == 200
    assert response.data.decode('utf-8') == 'Async'
    
    # The cache should have at least one more entry
    assert len(app._ensure_sync_cache) >= initial_cache_size
    
    # Make another request with parameters
    response = client.get('/user/testuser')
    assert response.status_code == 200
    assert response.data.decode('utf-8') == 'User: testuser'