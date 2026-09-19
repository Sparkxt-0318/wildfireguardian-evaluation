"""Loading experiment records and fingerprinting their source.

Every analysis must be traceable back to bytes on disk, so loading and
checksumming are the same operation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from wg_eval.schema import SCHEMA_BY_NAME

_CHUNK = 1 << 20


def file_checksum(path: str | Path, algorithm: str = "sha256") -> str:
    """Return ``"<algorithm>:<hexdigest>"`` for a file, read in chunks."""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return f"{algorithm}:{digest.hexdigest()}"


@dataclass(frozen=True)
class DataSource:
    """A loaded record table together with the fingerprint of its source."""

    path: Path
    checksum: str
    n_rows: int
    format: str
    columns: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "checksum": self.checksum,
            "n_rows": self.n_rows,
            "format": self.format,
            "columns": list(self.columns),
        }


def _read(path: Path) -> tuple[pd.DataFrame, str]:
    suffix = path.suffix.lower()
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(path), "parquet"
    if suffix in (".csv", ".txt"):
        return pd.read_csv(path), "csv"
    if suffix in (".tsv",):
        return pd.read_csv(path, sep="\t"), "tsv"
    if suffix in (".jsonl", ".ndjson"):
        return pd.read_json(path, lines=True), "jsonl"
    raise ValueError(
        f"unsupported results format {suffix!r} for {path}; "
        "expected .parquet, .csv, .tsv or .jsonl"
    )


def coerce_types(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce known schema columns to their declared kinds, leaving extras alone.

    Coercion is lenient: a column that cannot be coerced is left as-is so that
    :func:`wg_eval.validate.validate_records` can report it precisely rather
    than the loader failing with a stack trace.
    """
    out = frame.copy()
    for name in out.columns:
        spec = SCHEMA_BY_NAME.get(str(name))
        if spec is None:
            continue
        col = out[name]
        try:
            if spec.kind == "id":
                out[name] = col.astype("string")
            elif spec.kind == "categorical":
                out[name] = col.astype("string")
            elif spec.kind == "numeric":
                out[name] = pd.to_numeric(col, errors="coerce").astype("float64")
            elif spec.kind == "boolean":
                numeric = pd.to_numeric(col, errors="coerce")
                out[name] = numeric.astype("float64")
        except (TypeError, ValueError):
            continue
    return out


def load_records(path: str | Path, *, coerce: bool = True) -> tuple[pd.DataFrame, DataSource]:
    """Load a record table and return it with its :class:`DataSource`.

    Parameters
    ----------
    path:
        ``.parquet``, ``.csv``, ``.tsv`` or ``.jsonl`` file of experiment records.
    coerce:
        Coerce known schema columns to their declared dtypes.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"results file not found: {path}")
    frame, fmt = _read(path)
    if coerce:
        frame = coerce_types(frame)
    source = DataSource(
        path=path.resolve(),
        checksum=file_checksum(path),
        n_rows=int(len(frame)),
        format=fmt,
        columns=tuple(str(c) for c in frame.columns),
    )
    return frame, source


def write_records(frame: pd.DataFrame, path: str | Path) -> DataSource:
    """Write records to ``path`` (format inferred from suffix) and fingerprint them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix in (".parquet", ".pq"):
        frame.to_parquet(path, index=False)
        fmt = "parquet"
    elif suffix == ".csv":
        frame.to_csv(path, index=False)
        fmt = "csv"
    elif suffix in (".jsonl", ".ndjson"):
        frame.to_json(path, orient="records", lines=True)
        fmt = "jsonl"
    else:
        raise ValueError(f"unsupported output format {suffix!r}")
    return DataSource(
        path=path.resolve(),
        checksum=file_checksum(path),
        n_rows=int(len(frame)),
        format=fmt,
        columns=tuple(str(c) for c in frame.columns),
    )
