"""Local development entry point. Production imports app.app through WSGI."""

from app import app


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)