"""Local development entry point. Production imports app.app through WSGI."""

from app import app


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=1100, debug=False)