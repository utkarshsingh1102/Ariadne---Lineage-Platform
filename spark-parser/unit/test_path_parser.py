"""
Path / URI parsing (plan §5.4 file-path Table IDs).
Scheme + bucket + normalised path; trailing slashes and query strings stripped.
"""
import pytest


def test_s3_path_parsed():
    from spark_parser.utils.path_parser import parse_path
    info = parse_path("s3://raw/orders/")
    assert info.scheme == "s3"
    assert info.bucket == "raw"
    assert info.path == "orders"  # trailing slash stripped


def test_abfss_path_parsed():
    from spark_parser.utils.path_parser import parse_path
    info = parse_path("abfss://lake@acct.dfs.core.windows.net/products/v2/")
    assert info.scheme == "abfss"
    assert info.bucket in {"lake", "lake@acct.dfs.core.windows.net"}
    assert "products" in info.path


def test_gs_path_parsed():
    from spark_parser.utils.path_parser import parse_path
    info = parse_path("gs://bucket-name/folder/file.parquet")
    assert info.scheme == "gs"
    assert info.bucket == "bucket-name"
    assert info.path.endswith("file.parquet")


def test_file_uri_parsed():
    from spark_parser.utils.path_parser import parse_path
    info = parse_path("file:///data/local/orders/")
    assert info.scheme == "file"
    assert info.path.endswith("orders")


def test_query_string_stripped():
    from spark_parser.utils.path_parser import parse_path
    a = parse_path("s3://raw/orders/?version=2")
    b = parse_path("s3://raw/orders/")
    assert a.path == b.path


def test_canonical_id_string_stable():
    """Plan §5.4: paths with/without trailing slash must produce same canonical ID."""
    from spark_parser.utils.path_parser import canonical_path_id
    a = canonical_path_id("s3://raw/orders/")
    b = canonical_path_id("s3://raw/orders")
    assert a == b


def test_jdbc_url_parsed():
    """JDBC URLs are also valid Table locations."""
    from spark_parser.utils.path_parser import parse_path
    info = parse_path("jdbc:postgresql://host:5432/dbname?table=t")
    assert info.scheme == "jdbc"
