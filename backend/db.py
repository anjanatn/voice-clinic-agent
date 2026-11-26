from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .models import Appointment, Doctor, TimeSlot

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "clinic.db"

DOCTORS: list[tuple[str, str, str, str]] = [
    ("DOC-PATEL", "Dr. Maya Patel", "General Medicine", "Suite 101"),
    ("DOC-ROSTOVA", "Dr. Elena Rostova", "Dentistry", "Suite 204"),
    ("DOC-CHEN", "Dr. Marcus Chen", "Dermatology", "Suite 302"),
    ("DOC-NAIR", "Dr. Priya Nair", "Pediatrics", "Suite 108"),
]

DAILY_TIMES: list[tuple[str, str]] = [
    ("09:00", "09:30"),
    ("10:00", "10:30"),
    ("11:30", "12:00"),
    ("14:00", "14:30"),
    ("15:30", "16:00"),
    ("17:00", "17:30"),
]


def get_db_path() -> Path:
    env_path = os.getenv("CLINIC_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path | None = None, force_reset: bool = False) -> None:
    with get_connection(db_path) as conn:
        if force_reset:
            conn.executescript(
                """
                DROP TABLE IF EXISTS appointments;
                DROP TABLE IF EXISTS slots;
                DROP TABLE IF EXISTS doctors;
                """
            )

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                specialty TEXT NOT NULL,
                room TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS slots (
                id TEXT PRIMARY KEY,
                doctor_id TEXT NOT NULL REFERENCES doctors(id),
                date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                is_booked INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS appointments (
                booking_id TEXT PRIMARY KEY,
                slot_id TEXT NOT NULL UNIQUE REFERENCES slots(id),
                patient_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'confirmed',
                created_at TEXT NOT NULL
            );
            """
        )

        existing = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
        if existing == 0:
            _seed_data(conn)
        conn.commit()


def _seed_data(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO doctors (id, name, specialty, room) VALUES (?, ?, ?, ?)",
        DOCTORS,
    )

    today = datetime.now().date()
    slot_rows: list[tuple[str, str, str, str, str, int]] = []
    counter = 1
    for day_offset in range(0, 3):
        slot_date = (today + timedelta(days=day_offset)).isoformat()
        for doc_id, _, _, _ in DOCTORS:
            for start_time, end_time in DAILY_TIMES:
                slot_id = f"SLOT-{counter:03d}"
                slot_rows.append((slot_id, doc_id, slot_date, start_time, end_time, 0))
                counter += 1

    conn.executemany(
        "INSERT INTO slots (id, doctor_id, date, start_time, end_time, is_booked) VALUES (?, ?, ?, ?, ?, ?)",
        slot_rows,
    )

    # Seed two existing appointments so lookup/reschedule/cancel work immediately
    now_iso = datetime.now().isoformat(timespec="seconds")
    sample_bookings = [
        ("BK-1042", "SLOT-001", "Alex Rivera", "555-0192", "Annual physical checkup", "confirmed", now_iso),
        ("BK-1043", "SLOT-008", "Sarah Jenkins", "555-0148", "Dental cleaning & exam", "confirmed", now_iso),
    ]
    for booking_id, slot_id, patient_name, phone, reason, status, created_at in sample_bookings:
        conn.execute("UPDATE slots SET is_booked = 1 WHERE id = ?", (slot_id,))
        conn.execute(
            """
            INSERT INTO appointments (booking_id, slot_id, patient_name, phone, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (booking_id, slot_id, patient_name, phone, reason, status, created_at),
        )


def resolve_date_alias(date_str: str | None) -> str | None:
    if not date_str:
        return None
    normalized = date_str.strip().lower()
    today = datetime.now().date()
    if normalized == "today":
        return today.isoformat()
    if normalized == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    return date_str.strip()


def list_doctors(db_path: Path | None = None) -> list[Doctor]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT id, name, specialty, room FROM doctors ORDER BY name").fetchall()
        return [Doctor(**dict(row)) for row in rows]


def query_slots(
    specialty: str | None = None,
    doctor_name: str | None = None,
    date: str | None = None,
    include_booked: bool = False,
    db_path: Path | None = None,
) -> list[TimeSlot]:
    resolved_date = resolve_date_alias(date)
    clauses: list[str] = []
    params: list[object] = []

    if not include_booked:
        clauses.append("s.is_booked = 0")
    if specialty:
        clauses.append("LOWER(d.specialty) LIKE ?")
        params.append(f"%{specialty.strip().lower()}%")
    if doctor_name:
        clauses.append("LOWER(d.name) LIKE ?")
        params.append(f"%{doctor_name.strip().lower()}%")
    if resolved_date:
        clauses.append("s.date = ?")
        params.append(resolved_date)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT s.id, s.doctor_id, d.name AS doctor_name, d.specialty,
               s.date, s.start_time, s.end_time, s.is_booked
        FROM slots s
        JOIN doctors d ON d.id = s.doctor_id
        {where_sql}
        ORDER BY s.date ASC, s.start_time ASC, d.name ASC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [
            TimeSlot(
                id=row["id"],
                doctor_id=row["doctor_id"],
                doctor_name=row["doctor_name"],
                specialty=row["specialty"],
                date=row["date"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                is_booked=bool(row["is_booked"]),
            )
            for row in rows
        ]


def list_appointments(db_path: Path | None = None) -> list[Appointment]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.booking_id, a.slot_id, s.doctor_id, d.name AS doctor_name, d.specialty,
                   s.date, s.start_time, s.end_time, a.patient_name, a.phone, a.reason,
                   a.status, a.created_at
            FROM appointments a
            JOIN slots s ON s.id = a.slot_id
            JOIN doctors d ON d.id = s.doctor_id
            ORDER BY s.date ASC, s.start_time ASC
            """
        ).fetchall()
        return [Appointment(**dict(row)) for row in rows]


def lookup_appointments(
    booking_id: str | None = None,
    phone: str | None = None,
    patient_name: str | None = None,
    db_path: Path | None = None,
) -> list[Appointment]:
    clauses: list[str] = []
    params: list[object] = []

    if booking_id:
        clauses.append("UPPER(a.booking_id) = ?")
        params.append(booking_id.strip().upper())
    if phone:
        clean_digits = "".join(ch for ch in phone if ch.isdigit())
        if clean_digits:
            clauses.append("REPLACE(REPLACE(REPLACE(a.phone, '-', ''), ' ', ''), '+', '') LIKE ?")
            params.append(f"%{clean_digits}%")
        else:
            clauses.append("a.phone LIKE ?")
            params.append(f"%{phone.strip()}%")
    if patient_name:
        clauses.append("LOWER(a.patient_name) LIKE ?")
        params.append(f"%{patient_name.strip().lower()}%")

    if not clauses:
        return []

    where_sql = " OR ".join(clauses)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT a.booking_id, a.slot_id, s.doctor_id, d.name AS doctor_name, d.specialty,
                   s.date, s.start_time, s.end_time, a.patient_name, a.phone, a.reason,
                   a.status, a.created_at
            FROM appointments a
            JOIN slots s ON s.id = a.slot_id
            JOIN doctors d ON d.id = s.doctor_id
            WHERE {where_sql}
            ORDER BY s.date ASC, s.start_time ASC
            """,
            params,
        ).fetchall()
        return [Appointment(**dict(row)) for row in rows]


def _next_booking_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT booking_id FROM appointments").fetchall()
    max_num = 1043
    for row in rows:
        bid = row["booking_id"]
        if bid.startswith("BK-") and bid[3:].isdigit():
            max_num = max(max_num, int(bid[3:]))
    return f"BK-{max_num + 1}"


def book_slot(
    slot_id: str,
    patient_name: str,
    phone: str,
    reason: str,
    db_path: Path | None = None,
) -> Appointment:
    normalized_slot = slot_id.strip().upper()
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        slot_row = conn.execute(
            "SELECT id, is_booked FROM slots WHERE id = ?",
            (normalized_slot,),
        ).fetchone()
        if slot_row is None:
            raise LookupError(f"Slot {normalized_slot} not found.")
        if slot_row["is_booked"]:
            raise ValueError(f"Slot {normalized_slot} is already booked.")

        booking_id = _next_booking_id(conn)
        created_at = datetime.now().isoformat(timespec="seconds")
        conn.execute("UPDATE slots SET is_booked = 1 WHERE id = ?", (normalized_slot,))
        conn.execute(
            """
            INSERT INTO appointments (booking_id, slot_id, patient_name, phone, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'confirmed', ?)
            """,
            (booking_id, normalized_slot, patient_name.strip(), phone.strip(), reason.strip(), created_at),
        )
        conn.commit()

    results = lookup_appointments(booking_id=booking_id, db_path=db_path)
    return results[0]


def reschedule_appointment(
    booking_id: str,
    new_slot_id: str,
    db_path: Path | None = None,
) -> Appointment:
    normalized_bid = booking_id.strip().upper()
    normalized_new_slot = new_slot_id.strip().upper()

    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        appt_row = conn.execute(
            "SELECT booking_id, slot_id FROM appointments WHERE UPPER(booking_id) = ?",
            (normalized_bid,),
        ).fetchone()
        if appt_row is None:
            raise LookupError(f"Booking {normalized_bid} not found.")

        old_slot_id = appt_row["slot_id"]
        if old_slot_id == normalized_new_slot:
            conn.rollback()
            return lookup_appointments(booking_id=normalized_bid, db_path=db_path)[0]

        new_slot_row = conn.execute(
            "SELECT id, is_booked FROM slots WHERE id = ?",
            (normalized_new_slot,),
        ).fetchone()
        if new_slot_row is None:
            raise LookupError(f"Slot {normalized_new_slot} not found.")
        if new_slot_row["is_booked"]:
            raise ValueError(f"Slot {normalized_new_slot} is already booked.")

        conn.execute("UPDATE slots SET is_booked = 0 WHERE id = ?", (old_slot_id,))
        conn.execute("UPDATE slots SET is_booked = 1 WHERE id = ?", (normalized_new_slot,))
        conn.execute(
            "UPDATE appointments SET slot_id = ?, status = 'rescheduled' WHERE booking_id = ?",
            (normalized_new_slot, appt_row["booking_id"]),
        )
        conn.commit()

    return lookup_appointments(booking_id=normalized_bid, db_path=db_path)[0]


def cancel_appointment(booking_id: str, db_path: Path | None = None) -> Appointment:
    normalized_bid = booking_id.strip().upper()
    existing = lookup_appointments(booking_id=normalized_bid, db_path=db_path)
    if not existing:
        raise LookupError(f"Booking {normalized_bid} not found.")
    cancelled = existing[0].model_copy(update={"status": "cancelled"})

    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE slots SET is_booked = 0 WHERE id = ?", (cancelled.slot_id,))
        conn.execute("DELETE FROM appointments WHERE UPPER(booking_id) = ?", (normalized_bid,))
        conn.commit()

    return cancelled
