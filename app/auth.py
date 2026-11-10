"""Role-specific authentication helpers."""

from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def researcher_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if getattr(current_user, "account_type", None) != "researcher":
            abort(403)
        return view(*args, **kwargs)

    return wrapped
