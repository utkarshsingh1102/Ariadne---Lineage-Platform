"""Phase 2 unit tests — constraint inference engine."""
from __future__ import annotations

from qlikview_parser.constraints import infer_constraints
from qlikview_parser.ids import dataset_qname
from qlikview_parser.models import (
    Attribute,
    Dataset,
    Join,
    QlikViewApp,
)
from qlikview_parser.qvd_header import QvdField, QvdHeader


def _make_app() -> QlikViewApp:
    return QlikViewApp(app_name="test", file_path="/tmp/test.qvs")


def _attr(ds_q: str, name: str, ordinal: int = 0) -> Attribute:
    return Attribute(dataset=ds_q, name=name, ordinal=ordinal)


# ---------------------------------------------------------------------------
# Signal 1 — QVD-hint unique candidates
# ---------------------------------------------------------------------------


def test_qvd_hint_emits_unique_candidate():
    app = _make_app()
    ds_q = dataset_qname(app.file_path, "Customers")
    app.datasets.append(Dataset(name="Customers", origin="qvd_store", app=app.file_path))
    app.attributes.append(_attr(ds_q, "CustomerID"))

    headers = {
        "qvd/customers.qvd": QvdHeader(
            table_name="Customers",
            no_of_records=10000,
            fields=[
                QvdField(name="CustomerID", ordinal=0,
                         no_of_symbols=10000, is_likely_unique=True),
            ],
        ),
    }
    constraints, diags = infer_constraints(app, qvd_headers=headers)

    qvd_hint = [c for c in constraints if c.source == "qvd_hint"]
    assert len(qvd_hint) == 1
    assert qvd_hint[0].kind == "unique"
    assert qvd_hint[0].columns == ("CustomerID",)
    assert qvd_hint[0].confidence == 0.7
    assert qvd_hint[0].dataset == ds_q


# ---------------------------------------------------------------------------
# Signal 2 — JOIN-inferred FK
# ---------------------------------------------------------------------------


def test_join_inferred_fk_emitted_from_shared_field():
    app = _make_app()
    customers_q = dataset_qname(app.file_path, "Customers")
    orders_q = dataset_qname(app.file_path, "Orders")
    app.datasets.extend([
        Dataset(name="Customers", origin="load", app=app.file_path),
        Dataset(name="Orders", origin="load", app=app.file_path),
    ])
    app.attributes.extend([
        _attr(customers_q, "CustomerID"),
        _attr(customers_q, "Name"),
        _attr(orders_q, "OrderID"),
        _attr(orders_q, "CustomerID"),     # ← shared with Customers
    ])
    app.joins.append(Join(
        target_table="Orders", source_table="Customers", join_type="LEFT JOIN",
    ))

    constraints, _ = infer_constraints(app)

    fk = [c for c in constraints if c.source == "join_inferred" and c.kind == "foreign"]
    assert any(
        c.columns == ("CustomerID",) and c.dataset == orders_q
        and c.references and c.references[0] == customers_q
        for c in fk
    ), f"missing FK Orders.CustomerID → Customers.CustomerID; got {fk}"


# ---------------------------------------------------------------------------
# Signal 3 — auto-association
# ---------------------------------------------------------------------------


def test_auto_association_emits_fk_candidates_in_both_directions():
    app = _make_app()
    a_q = dataset_qname(app.file_path, "Sales")
    b_q = dataset_qname(app.file_path, "Products")
    app.datasets.extend([
        Dataset(name="Sales", origin="load", app=app.file_path),
        Dataset(name="Products", origin="load", app=app.file_path),
    ])
    app.attributes.extend([
        _attr(a_q, "ProductID"),
        _attr(b_q, "ProductID"),
    ])

    constraints, _ = infer_constraints(app)
    fk = [c for c in constraints if c.source == "naming_inferred"
          and c.kind == "foreign"]
    # One in each direction.
    pairs = {(c.dataset, c.references[0]) for c in fk if c.references}
    assert (a_q, b_q) in pairs
    assert (b_q, a_q) in pairs


# ---------------------------------------------------------------------------
# Signal 4 — synthetic-key detection
# ---------------------------------------------------------------------------


def test_synthetic_key_fires_when_two_fields_shared_between_two_tables():
    app = _make_app()
    a_q = dataset_qname(app.file_path, "Sales")
    b_q = dataset_qname(app.file_path, "Inventory")
    app.datasets.extend([
        Dataset(name="Sales", origin="load", app=app.file_path),
        Dataset(name="Inventory", origin="load", app=app.file_path),
    ])
    # Both tables have ProductID AND Region → QV would fabricate $Syn.
    app.attributes.extend([
        _attr(a_q, "ProductID"),
        _attr(a_q, "Region"),
        _attr(a_q, "Amount"),
        _attr(b_q, "ProductID"),
        _attr(b_q, "Region"),
        _attr(b_q, "OnHand"),
    ])

    constraints, diags = infer_constraints(app)
    synthetic = [c for c in constraints if c.kind == "synthetic"]
    assert len(synthetic) == 2     # one per table
    assert any(c.dataset == a_q for c in synthetic)
    assert any(c.dataset == b_q for c in synthetic)
    # Both shared fields captured.
    for c in synthetic:
        assert set(c.columns) == {"ProductID", "Region"}
    # And a single warn-level diagnostic.
    synkey_diags = [d for d in diags if d.code == "QV-SYNKEY"]
    assert len(synkey_diags) == 1
    assert synkey_diags[0].level == "warn"


def test_no_synthetic_key_when_only_one_field_shared():
    """A single shared field is normal auto-association, not a synthetic key."""
    app = _make_app()
    a_q = dataset_qname(app.file_path, "Sales")
    b_q = dataset_qname(app.file_path, "Customers")
    app.datasets.extend([
        Dataset(name="Sales", origin="load", app=app.file_path),
        Dataset(name="Customers", origin="load", app=app.file_path),
    ])
    app.attributes.extend([
        _attr(a_q, "CustomerID"),
        _attr(a_q, "Amount"),
        _attr(b_q, "CustomerID"),
        _attr(b_q, "Name"),
    ])
    constraints, diags = infer_constraints(app)
    assert not any(c.kind == "synthetic" for c in constraints)
    assert not any(d.code == "QV-SYNKEY" for d in diags)


# ---------------------------------------------------------------------------
# Signal 5 — naming heuristics
# ---------------------------------------------------------------------------


def test_naming_heuristic_emits_low_confidence_pk_candidates():
    app = _make_app()
    ds_q = dataset_qname(app.file_path, "Customers")
    app.datasets.append(Dataset(name="Customers", origin="load", app=app.file_path))
    app.attributes.extend([
        _attr(ds_q, "CustomerID"),    # matches *_ID
        _attr(ds_q, "ProductKey"),    # matches *Key
        _attr(ds_q, "%REGIONKEY"),    # matches %FOOKEY
        _attr(ds_q, "ID"),            # matches plain ID
        _attr(ds_q, "Name"),          # NOT a key
    ])

    constraints, _ = infer_constraints(app)
    naming_pk = [c for c in constraints
                 if c.source == "naming_inferred" and c.kind == "primary"]
    col_names = {c.columns[0] for c in naming_pk}
    assert "CustomerID" in col_names
    assert "ProductKey" in col_names
    assert "%REGIONKEY" in col_names
    assert "ID" in col_names
    assert "Name" not in col_names
    # All naming-heuristic PKs are 0.4 confidence
    assert all(c.confidence == 0.4 for c in naming_pk)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_engine_dedups_repeated_candidates():
    """Multiple signals can propose the SAME (dataset, kind, columns).
    The engine must dedup so the graph doesn't get N copies of the
    same constraint with subtly different confidences."""
    app = _make_app()
    ds_q = dataset_qname(app.file_path, "Customers")
    app.datasets.append(Dataset(name="Customers", origin="load", app=app.file_path))
    app.attributes.append(_attr(ds_q, "CustomerID"))

    # Same field appears in QVD-hint AND naming-heuristic-PK paths.
    headers = {"qvd/x.qvd": QvdHeader(
        table_name="Customers", no_of_records=100,
        fields=[QvdField(name="CustomerID", ordinal=0,
                         no_of_symbols=100, is_likely_unique=True)],
    )}
    constraints, _ = infer_constraints(app, qvd_headers=headers)

    # We expect: 1 unique (QVD hint) + 1 primary (naming) = 2 distinct
    # constraints (different kinds), not 4 from double-emission.
    customer_id_constraints = [c for c in constraints if c.columns == ("CustomerID",)]
    by_kind = {c.kind for c in customer_id_constraints}
    assert by_kind == {"unique", "primary"}
