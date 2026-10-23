"""The room in its own desktop window (pywebview: WebKit on macOS, WebView2 on Windows, GTK/Qt on Linux).

Without pywebview installed, the room opens in the browser instead; everything else works the same.
"""


def available():
    import importlib.util
    return importlib.util.find_spec("webview") is not None


def run(url):
    import webview
    webview.create_window("Agent Control Room", url, width=1180, height=860, min_size=(420, 420),
                          background_color="#161616")
    webview.start()
