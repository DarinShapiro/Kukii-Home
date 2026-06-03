"""Identity-resolution state: subjects, templates, and persisted resolutions.

The :class:`~kukiihome_preprocessor.detection_store.DetectionStore` records what
the cameras *observed* (detections + the always-embedded `track_embeddings`).
This store records who those observations *are* — the enrollment + resolution
side of the loop, and the backing for the operator Review UI:

  subjects           — a person OR pet the operator has named (KnownActor /
                       KnownPet); `kind` discriminates, species/owner for pets.
  subject_templates  — one averaged, L2-normalized template per (subject,
                       modality). The thing `resolve_event` matches against.
  resolutions        — the persisted output of `resolve_event`: which track
                       resolved to which subject, at what confidence, with a
                       correctable verdict. Powers the timeline + review queue.

Local-first: it opens the **same SQLite file** the DetectionStore writes (WAL
lets the two coexist), so `track_summaries` can join the observed
`track_embeddings` / `detections` against resolutions in one place without a
second database. Enrollment is just "label a stored track" — its embeddings
*are* the template source, so this store reads embeddings the worker already
persisted rather than re-running any model.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from kukiihome_preprocessor.pipelines.identity import resolve_event

if TYPE_CHECKING:
    from kukiihome_shared.preprocessor import ActorMatch

    from kukiihome_preprocessor.detection_store import DetectionStore
    from kukiihome_preprocessor.pipelines.identity import EnrolledCorpus


# match_method → the enrollment modality it resolves in. Kept here so a
# persisted resolution row carries the modality without the matcher having to.
_METHOD_MODALITY: dict[str, str] = {
    "body_id_osnet": "body",
    "ccreid_cal": "body_shape",
    "gait_opengait": "gait",
    "face_arcface": "face",
    "pet_dinov2": "pet",
    "plate_lpr": "plate",
    "height_calib": "height",
}

# modality → the ActorEnrollmentEvent field that carries its template. The
# inverse of the router's _MODALITY_SOURCE — used to fold a labelled subject
# into the live recognition cache.
_MODALITY_EVENT_ATTR: dict[str, str] = {
    "body": "body_embedding",
    "body_shape": "body_shape_embedding",
    "gait": "gait_embedding",
    "face": "face_embedding",
    "pet": "pet_dinov2_centroid",
}

# Detection kinds → the subject kind the operator labels them as.
_PET_DET_KINDS = frozenset({"dog", "cat"})


_SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (
    subject_id   TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,        -- person | pet
    display_name TEXT NOT NULL,
    species      TEXT,                 -- pet only: dog | cat
    owner_id     TEXT,                 -- pet only: owning person subject_id
    created_ts   REAL NOT NULL,
    updated_ts   REAL NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS subject_templates (
    subject_id     TEXT NOT NULL,
    modality       TEXT NOT NULL,      -- body | pet | gait | face | body_shape
    dim            INTEGER NOT NULL,
    embedding      BLOB NOT NULL,      -- averaged + L2-normalized, float32 LE
    source_track_n INTEGER NOT NULL,
    updated_ts     REAL NOT NULL,
    PRIMARY KEY (subject_id, modality)
);
CREATE TABLE IF NOT EXISTS resolutions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL,
    camera_id    TEXT,
    track_id     TEXT NOT NULL,
    frame_ts     REAL NOT NULL,
    modality     TEXT NOT NULL,
    match_method TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    confidence   REAL NOT NULL,
    verdict      TEXT NOT NULL,        -- auto | confirmed | rejected | reassigned
    resolved_ts  REAL NOT NULL,
    UNIQUE(event_id, track_id, frame_ts, modality)
);
CREATE TABLE IF NOT EXISTS template_provenance (
    subject_id  TEXT NOT NULL,
    modality    TEXT NOT NULL,
    event_id    TEXT NOT NULL,
    track_id    TEXT NOT NULL,
    frame_count INTEGER NOT NULL,
    added_ts    REAL NOT NULL,
    PRIMARY KEY (subject_id, modality, event_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_res_event   ON resolutions(event_id, track_id);
CREATE INDEX IF NOT EXISTS idx_res_subject ON resolutions(subject_id);
CREATE INDEX IF NOT EXISTS idx_prov_subject ON template_provenance(subject_id);
"""


@dataclass
class TrackSummary:
    """One observed track, with its evidence + current resolution — a card in
    the Review UI."""

    event_id: str
    camera_id: str
    track_id: str
    kind: str                 # person | pet (mapped from the detection class)
    n_frames: int
    t0: float
    t1: float
    modalities: list[str]     # which embedding modalities this track carries
    best_frame_name: str | None
    best_bbox: tuple[float, float, float, float] | None
    # current resolution, if any
    subject_id: str | None = None
    subject_name: str | None = None
    confidence: float | None = None
    verdict: str | None = None

    @property
    def status(self) -> str:
        return "resolved" if self.subject_id else "unresolved"


@dataclass
class SubjectSummary:
    subject_id: str
    kind: str
    display_name: str
    species: str | None
    owner_id: str | None
    modalities: list[str] = field(default_factory=list)
    appearances: int = 0


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "subject"


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v if n < 1e-8 else (v / n)


class IdentityStore:
    """Subjects + templates + resolutions, over the detections.db file."""

    def __init__(self, path: str | Path = "detections.db") -> None:
        self._path = str(path)
        # check_same_thread=False: served from the FastAPI event-loop thread;
        # access is serialized. See DetectionStore for the same rationale.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── observed tracks (joins the DetectionStore's tables) ─────────────

    def track_summaries(
        self,
        *,
        status: str | None = None,   # unresolved | resolved | None=all
        kind: str | None = None,     # person | pet | None=all
        limit: int = 200,
    ) -> list[TrackSummary]:
        """Group persisted embeddings into per-track cards, joined to the
        best detection crop + any current resolution. Newest tracks first."""
        rows = self._conn.execute(
            """SELECT event_id, camera_id, track_id,
                      COUNT(DISTINCT frame_ts) AS n_frames,
                      MIN(frame_ts) AS t0, MAX(frame_ts) AS t1,
                      GROUP_CONCAT(DISTINCT modality) AS modalities
               FROM track_embeddings
               GROUP BY event_id, track_id
               ORDER BY t1 DESC
               LIMIT ?""",
            (max(1, limit) * 4,),  # over-fetch; post-filters trim below
        ).fetchall()

        out: list[TrackSummary] = []
        for r in rows:
            det = self._best_detection(r["event_id"], r["track_id"])
            det_kind = det["kind"] if det else None
            subject_kind = self._subject_kind(det_kind)
            if kind is not None and subject_kind != kind:
                continue
            res = self._best_resolution(r["event_id"], r["track_id"])
            summary = TrackSummary(
                event_id=r["event_id"], camera_id=r["camera_id"], track_id=r["track_id"],
                kind=subject_kind, n_frames=r["n_frames"], t0=r["t0"], t1=r["t1"],
                modalities=sorted((r["modalities"] or "").split(",")) if r["modalities"] else [],
                best_frame_name=det["frame_name"] if det else None,
                best_bbox=_json_bbox(det["bbox"]) if det else None,
                subject_id=res["subject_id"] if res else None,
                subject_name=res["display_name"] if res else None,
                confidence=res["confidence"] if res else None,
                verdict=res["verdict"] if res else None,
            )
            if status is not None and summary.status != status:
                continue
            out.append(summary)
            if len(out) >= limit:
                break
        return out

    def _best_detection(self, event_id: str, track_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT kind, frame_name, bbox, camera_id, confidence
               FROM detections WHERE event_id=? AND track_id=?
               ORDER BY confidence DESC LIMIT 1""",
            (event_id, track_id),
        ).fetchone()

    @staticmethod
    def _subject_kind(det_kind: str | None) -> str:
        if det_kind in _PET_DET_KINDS:
            return "pet"
        return "person"

    # ── subjects + enrollment ───────────────────────────────────────────

    def upsert_subject(
        self,
        *,
        display_name: str,
        kind: str,
        subject_id: str | None = None,
        species: str | None = None,
        owner_id: str | None = None,
    ) -> str:
        sid = subject_id or _slug(display_name)
        now = time.time()
        self._conn.execute(
            """INSERT INTO subjects
                 (subject_id, kind, display_name, species, owner_id, created_ts, updated_ts, active)
               VALUES (?,?,?,?,?,?,?,1)
               ON CONFLICT(subject_id) DO UPDATE SET
                 display_name=excluded.display_name, kind=excluded.kind,
                 species=COALESCE(excluded.species, subjects.species),
                 owner_id=COALESCE(excluded.owner_id, subjects.owner_id),
                 updated_ts=excluded.updated_ts, active=1""",
            (sid, kind, display_name, species, owner_id, now, now),
        )
        self._conn.commit()
        return sid

    def enroll_from_track(
        self,
        store: DetectionStore,
        *,
        subject_id: str,
        event_id: str,
        track_id: str,
        modalities: list[str] | None = None,
    ) -> list[str]:
        """Build/extend the subject's templates from a stored track's
        embeddings. Averages each modality's vectors for the track (the same
        per-track mean enrollment does over crops), L2-normalizes, writes the
        template. Returns the modalities enrolled."""
        rows = store.embeddings_for_event(event_id)
        by_modality: dict[str, list[np.ndarray]] = {}
        for r in rows:
            if r.track_id != track_id:
                continue
            if modalities is not None and r.modality not in modalities:
                continue
            by_modality.setdefault(r.modality, []).append(r.embedding)

        enrolled: list[str] = []
        now = time.time()
        for modality, vecs in by_modality.items():
            template = _unit(np.mean(np.stack(vecs), axis=0).astype(np.float32))
            self._conn.execute(
                """INSERT INTO subject_templates
                     (subject_id, modality, dim, embedding, source_track_n, updated_ts)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(subject_id, modality) DO UPDATE SET
                     dim=excluded.dim, embedding=excluded.embedding,
                     source_track_n=excluded.source_track_n, updated_ts=excluded.updated_ts""",
                (subject_id, modality, int(template.shape[0]),
                 np.ascontiguousarray(template, dtype="<f4").tobytes(), len(vecs), now),
            )
            # Provenance: which track built this template (for undo + merge
            # re-averaging). Idempotent on (subject, modality, event, track).
            self._conn.execute(
                """INSERT INTO template_provenance
                     (subject_id, modality, event_id, track_id, frame_count, added_ts)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(subject_id, modality, event_id, track_id) DO UPDATE SET
                     frame_count=excluded.frame_count, added_ts=excluded.added_ts""",
                (subject_id, modality, event_id, track_id, len(vecs), now),
            )
            enrolled.append(modality)
        self._conn.commit()
        return enrolled

    def list_subjects(self) -> list[SubjectSummary]:
        out: list[SubjectSummary] = []
        for s in self._conn.execute(
            "SELECT * FROM subjects WHERE active=1 ORDER BY updated_ts DESC"
        ).fetchall():
            mods = [
                m["modality"]
                for m in self._conn.execute(
                    "SELECT modality FROM subject_templates WHERE subject_id=? ORDER BY modality",
                    (s["subject_id"],),
                ).fetchall()
            ]
            (appearances,) = self._conn.execute(
                "SELECT COUNT(DISTINCT event_id || '/' || track_id) FROM resolutions "
                "WHERE subject_id=? AND verdict != 'rejected'",
                (s["subject_id"],),
            ).fetchone()
            out.append(SubjectSummary(
                subject_id=s["subject_id"], kind=s["kind"], display_name=s["display_name"],
                species=s["species"], owner_id=s["owner_id"], modalities=mods,
                appearances=appearances or 0,
            ))
        return out

    def build_corpus(self) -> EnrolledCorpus:
        from kukiihome_preprocessor.pipelines.identity import EnrolledCorpus

        templates: dict[str, dict[str, object]] = {}
        names: dict[str, str] = {}
        for r in self._conn.execute(
            """SELECT t.subject_id, t.modality, t.dim, t.embedding, s.display_name
               FROM subject_templates t JOIN subjects s ON s.subject_id=t.subject_id
               WHERE s.active=1""",
        ).fetchall():
            emb = np.frombuffer(r["embedding"], dtype="<f4")
            if emb.shape[0] != r["dim"]:
                continue
            templates.setdefault(r["modality"], {})[r["subject_id"]] = emb
            names[r["subject_id"]] = r["display_name"]
        return EnrolledCorpus(templates=templates, actor_names=names)

    def build_enrollment_event(self, subject_id: str):
        """An :class:`ActorEnrollmentEvent` carrying the subject's current
        templates — to fold a freshly-labelled subject into the **live**
        recognition cache so the next ``/frame_window`` enrich can match it.

        The canonical cross-service enrollment path stays memory→NATS (the
        preprocessor has no outbound NATS by design); this is the in-process,
        single-box update that makes a label take effect immediately on the
        live path. Returns ``None`` for an unknown subject."""
        from kukiihome_shared.preprocessor import ActorEnrollmentEvent

        s = self._conn.execute(
            "SELECT * FROM subjects WHERE subject_id=?", (subject_id,)
        ).fetchone()
        if s is None:
            return None
        kwargs: dict[str, object] = {}
        for r in self._conn.execute(
            "SELECT modality, dim, embedding FROM subject_templates WHERE subject_id=?",
            (subject_id,),
        ).fetchall():
            attr = _MODALITY_EVENT_ATTR.get(r["modality"])
            if not attr:
                continue
            emb = np.frombuffer(r["embedding"], dtype="<f4")
            if emb.shape[0] != r["dim"]:
                continue
            kwargs[attr] = tuple(float(x) for x in emb)
        return ActorEnrollmentEvent(
            actor_id=subject_id, action="enrolled", name=s["display_name"], **kwargs
        )

    # ── corrections (merge / split) ─────────────────────────────────────

    def clear_track_resolutions(self, event_id: str, track_id: str) -> int:
        """Delete a track's resolution rows so the next resolve recomputes it
        fresh. Called when (re)labelling a track: an explicit human label is
        authoritative, so it must override a prior ``rejected``/``confirmed``
        verdict that ``persist_resolutions`` would otherwise preserve."""
        cur = self._conn.execute(
            "DELETE FROM resolutions WHERE event_id=? AND track_id=?", (event_id, track_id)
        )
        self._conn.commit()
        return cur.rowcount

    def reject_track(self, event_id: str, track_id: str) -> int:
        """Mark a track's resolutions ``rejected`` → it drops back to the
        unresolved queue. The split-to-unknown correction: when a track was
        wrongly merged onto a subject (the OSNet 0.96 false-merge), reject it,
        then re-label it as its true identity. Returns rows affected."""
        cur = self._conn.execute(
            "UPDATE resolutions SET verdict='rejected' WHERE event_id=? AND track_id=?",
            (event_id, track_id),
        )
        self._conn.commit()
        return cur.rowcount

    def merge_subjects(self, from_id: str, into_id: str) -> bool:
        """Merge ``from_id`` into ``into_id``: repoint its resolutions, fold its
        templates into ``into`` (per-modality mean of the two, renormalized),
        move provenance, deactivate ``from``. The merge correction: two labels
        that are actually the same person/pet. Rejects cross-kind merges and
        self-merges. Returns False if either subject is unknown."""
        if from_id == into_id:
            return False
        a = self._conn.execute(
            "SELECT kind FROM subjects WHERE subject_id=?", (from_id,)
        ).fetchone()
        b = self._conn.execute(
            "SELECT kind FROM subjects WHERE subject_id=?", (into_id,)
        ).fetchone()
        if a is None or b is None:
            return False
        if a["kind"] != b["kind"]:
            raise ValueError("cannot merge a person with a pet")

        # Repoint, tolerating collisions where `into` already has a row for the
        # same key (UNIQUE on resolutions; PK on provenance): keep `into`'s,
        # drop `from`'s leftovers.
        self._conn.execute(
            "UPDATE OR IGNORE resolutions SET subject_id=? WHERE subject_id=?", (into_id, from_id)
        )
        self._conn.execute("DELETE FROM resolutions WHERE subject_id=?", (from_id,))
        now = time.time()
        for r in self._conn.execute(
            "SELECT modality, dim, embedding, source_track_n FROM subject_templates "
            "WHERE subject_id=?",
            (from_id,),
        ).fetchall():
            emb_from = np.frombuffer(r["embedding"], dtype="<f4")
            if emb_from.shape[0] != r["dim"]:
                continue
            existing = self._conn.execute(
                "SELECT dim, embedding, source_track_n FROM subject_templates "
                "WHERE subject_id=? AND modality=?",
                (into_id, r["modality"]),
            ).fetchone()
            if existing and existing["dim"] == r["dim"]:
                emb_into = np.frombuffer(existing["embedding"], dtype="<f4")
                merged = _unit((emb_from + emb_into).astype(np.float32))
                n = existing["source_track_n"] + r["source_track_n"]
            else:
                merged = emb_from
                n = r["source_track_n"]
            self._conn.execute(
                """INSERT INTO subject_templates
                     (subject_id, modality, dim, embedding, source_track_n, updated_ts)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(subject_id, modality) DO UPDATE SET
                     dim=excluded.dim, embedding=excluded.embedding,
                     source_track_n=excluded.source_track_n, updated_ts=excluded.updated_ts""",
                (into_id, r["modality"], int(merged.shape[0]),
                 np.ascontiguousarray(merged, dtype="<f4").tobytes(), n, now),
            )
        self._conn.execute(
            "UPDATE OR IGNORE template_provenance SET subject_id=? WHERE subject_id=?",
            (into_id, from_id),
        )
        self._conn.execute("DELETE FROM template_provenance WHERE subject_id=?", (from_id,))
        self._conn.execute("DELETE FROM subject_templates WHERE subject_id=?", (from_id,))
        self._conn.execute(
            "UPDATE subjects SET active=0, updated_ts=? WHERE subject_id=?", (now, from_id)
        )
        self._conn.commit()
        return True

    # ── resolution ──────────────────────────────────────────────────────

    def persist_resolutions(
        self, matches: tuple[ActorMatch, ...], *, camera_id: str | None, event_id: str
    ) -> int:
        """Write resolve_event output. ``auto`` verdict; UNIQUE(event,track,
        frame,modality) so re-resolving the same event upserts rather than
        duplicating."""
        now = time.time()
        n = 0
        for m in matches:
            modality = _METHOD_MODALITY.get(m.match_method, m.match_method)
            self._conn.execute(
                """INSERT INTO resolutions
                     (event_id, camera_id, track_id, frame_ts, modality, match_method,
                      subject_id, confidence, verdict, resolved_ts)
                   VALUES (?,?,?,?,?,?,?,?, 'auto', ?)
                   ON CONFLICT(event_id, track_id, frame_ts, modality) DO UPDATE SET
                     subject_id=excluded.subject_id, confidence=excluded.confidence,
                     match_method=excluded.match_method, resolved_ts=excluded.resolved_ts,
                     verdict=CASE WHEN resolutions.verdict IN ('confirmed','rejected','reassigned')
                                  THEN resolutions.verdict ELSE 'auto' END""",
                (event_id, camera_id, m.track_id, m.frame_ts, modality, m.match_method,
                 m.actor_id, m.confidence, now),
            )
            n += 1
        self._conn.commit()
        return n

    def resolve_persist(
        self, store: DetectionStore, *, event_id: str, camera_id: str | None = None
    ) -> int:
        """Run `resolve_event` for one event against the current corpus and
        persist the matches. Idempotent. Returns the number of matches."""
        corpus = self.build_corpus()
        matches = resolve_event(store, event_id, corpus)
        return self.persist_resolutions(matches, camera_id=camera_id, event_id=event_id)

    def events_with_embeddings(self) -> list[tuple[str, str | None]]:
        """(event_id, camera_id) for every event that has persisted
        embeddings — the back-fill scope for a fresh enrollment."""
        rows = self._conn.execute(
            "SELECT DISTINCT event_id, camera_id FROM track_embeddings"
        ).fetchall()
        return [(r["event_id"], r["camera_id"]) for r in rows]

    def resolve_all(self, store: DetectionStore) -> int:
        """Re-resolve every embedded event against the current corpus. The
        retroactive sweep run after an enrollment — names past appearances
        with no re-inference. Returns total matches persisted."""
        total = 0
        for eid, cam in self.events_with_embeddings():
            total += self.resolve_persist(store, event_id=eid, camera_id=cam)
        return total

    def crop_source(self, event_id: str, track_id: str) -> dict | None:
        """The representative crop for a track's thumbnail: the peak-confidence
        detection's frame_name + normalized bbox + camera + mapped kind. None
        if the track has no detection with a frame on disk."""
        det = self._best_detection(event_id, track_id)
        if det is None or not det["frame_name"]:
            return None
        return {
            "camera_id": det["camera_id"],
            "frame_name": det["frame_name"],
            "bbox": _json_bbox(det["bbox"]),
            "kind": self._subject_kind(det["kind"]),
        }

    def _best_resolution(self, event_id: str, track_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT r.subject_id, r.confidence, r.verdict, s.display_name
               FROM resolutions r JOIN subjects s ON s.subject_id=r.subject_id
               WHERE r.event_id=? AND r.track_id=? AND r.verdict != 'rejected'
               ORDER BY r.confidence DESC LIMIT 1""",
            (event_id, track_id),
        ).fetchone()


def _json_bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    if not raw:
        return None
    import json

    try:
        b = json.loads(raw)
        return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
    except (ValueError, IndexError, TypeError):
        return None
