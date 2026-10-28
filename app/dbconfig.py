"""
Single place that reads database settings from environment variables.

Works the same for a local XAMPP MySQL (defaults) and for a hosted MySQL such as
Aiven, which requires an encrypted (SSL/TLS) connection. To turn SSL on set:

    DB_SSL=1                 # enable TLS
    DB_SSL_CA=/path/ca.pem   # optional: verify the server certificate

Without DB_SSL_CA the connection is still encrypted, but the server's
certificate is not verified (fine for a hobby project, less strict).
"""
import os
import ssl


def _truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def ssl_arg():
    """Value for PyMySQL's `ssl=` argument (None = plain connection)."""
    if not _truthy(os.environ.get('DB_SSL')):
        return None
    ca = os.environ.get('DB_SSL_CA')
    if ca:
        return {'ca': ca}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def connect_kwargs():
    """Keyword arguments for pymysql.connect(), minus cursorclass/autocommit."""
    return dict(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=int(os.environ.get('DB_PORT', 3306)),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASSWORD', ''),
        database=os.environ.get('DB_NAME', 'watchnext'),
        charset='utf8mb4',
        ssl=ssl_arg(),
        connect_timeout=int(os.environ.get('DB_CONNECT_TIMEOUT', 15)),
    )
