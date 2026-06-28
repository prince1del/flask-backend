from flask import Blueprint, Response, redirect, render_template_string, request, session, url_for

from centralized_db_system.db import CentralizedDB
from app.utils import auth_enabled

auth_blueprint = Blueprint("auth", __name__)

LOGIN_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>NEXORA |Sign In</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    form { max-width: 320px; display: grid; gap: 0.75rem; }
    input { padding: 0.6rem; }
    button { padding: 0.6rem 1rem; }
    .error { color: #b91c1c; }
  </style>
</head>
<body>
  <h1>Sign in</h1>
  <form method="post">
    <input name="username" placeholder="Username" required />
    <input name="password" type="password" placeholder="Password" required />
    <button type="submit">Sign In</button>
  </form>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
</body>
</html>
"""


def register_auth_hooks(app) -> None:
    app.before_request(enforce_auth)
    app.before_request(ensure_default_admin)


def enforce_auth() -> Response | None:
    if not auth_enabled():
        return None

    if request.path in {"/login", "/logout"}:
        return None
    if request.path.startswith("/static/"):
        return None
    if request.path in {"/manifest.json", "/service-worker.js", "/icon-192.svg", "/icon-512.svg"}:
        return None

    if session.get("authenticated"):
        return None

    return redirect(url_for("auth.login"))


def ensure_default_admin() -> None:
    if not auth_enabled():
        return
    CentralizedDB().ensure_default_admin_user()


@auth_blueprint.route("/login", methods=["GET", "POST"], endpoint="login")
def login() -> str | Response:
    if not auth_enabled():
        session["authenticated"] = True
        return redirect("/")

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if CentralizedDB().authenticate_user(username, password):
            session["authenticated"] = True
            session["username"] = username
            return redirect(request.args.get("next") or "/")
        error = "Invalid username or password"

    return render_template_string(LOGIN_TEMPLATE, error=error)


@auth_blueprint.route("/logout", endpoint="logout")
def logout() -> Response:
    session.clear()
    return redirect(url_for("auth.login"))
