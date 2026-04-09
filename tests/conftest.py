import pytest


@pytest.fixture
def full_html():
    return """
    <html>
    <head>
        <title>Sample Product Page</title>
        <meta name="description" content="A quality kitchen toaster">
        <meta property="og:title" content="Sample OG Title">
        <meta property="og:description" content="Sample OG Description">
        <meta property="og:image" content="https://example.com/toaster.png">
        <link rel="canonical" href="https://example.com/toaster">
        <meta name="keywords" content="toaster, kitchen, appliance">
    </head>
    <body>
        <p>This toaster is perfect for your kitchen.</p>
        <script>var tracking = true;</script>
        <style>.hidden { display: none; }</style>
        <noscript>Enable JavaScript</noscript>
    </body>
    </html>
    """


@pytest.fixture
def minimal_html():
    return "<html><head><title>Minimal Page</title></head><body>Hello world content</body></html>"


@pytest.fixture
def no_meta_html():
    return "<html><head></head><body>Only body text here with several words</body></html>"
