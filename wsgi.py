from werkzeug.middleware.proxy_fix import ProxyFix

from app.web_app import create_app

app = create_app()
# Behind nginx / Render proxy: trust one hop of X-Forwarded-* so request.host_url
# is https:// (OAuth redirect URIs built from it must match Google's registered ones).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
