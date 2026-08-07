"""Regression guards on mail daemon and edge config.

These are config files rather than Python, but two properties are load-bearing
enough to pin: Dovecot must not recompute a path the database owns, and the
provisioning API must not be reachable from the internet.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOVECOT_SQL = REPO_ROOT / "docker" / "dovecot" / "dovecot-sql.conf.ext.tmpl"
NGINX_HTTPS = REPO_ROOT / "docker" / "nginx" / "templates" / "10-https.conf.template"


def test_dovecot_reads_maildir_path_rather_than_deriving_it():
    """A rename changes local_part but keeps maildir_path, so a derived path
    would send Dovecot to a new empty directory and orphan the user's mail."""
    content = DOVECOT_SQL.read_text(encoding="utf-8")
    user_query = content.split("user_query")[1]
    assert "m.maildir_path" in user_query
    assert "'/maildata/' || d.name" not in user_query


def test_provisioning_is_not_exposed_at_the_edge():
    """Provisioning is reachable only on the internal Docker network."""
    content = NGINX_HTTPS.read_text(encoding="utf-8")
    assert "/api/v1/provisioning/" in content
    provisioning_block = content.split("/api/v1/provisioning/")[1].split("}")[0]
    assert "return 404" in provisioning_block
    assert "proxy_pass" not in provisioning_block


def test_provisioning_block_precedes_the_general_api_block():
    """Nginx prefix matching takes the longest match, but ordering the block
    first keeps the intent obvious to the next reader."""
    content = NGINX_HTTPS.read_text(encoding="utf-8")
    assert content.index("/api/v1/provisioning/") < content.index("location /api/ {")
