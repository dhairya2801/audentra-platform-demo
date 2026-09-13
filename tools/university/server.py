"""Loopback-only model-university explorer and evaluation API. No live tenant access."""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from build import CLOCK, DEFAULT_OUTPUT, ROOT
from engine import (
    Rejected,
    cohort,
    connect,
    evidence,
    policies,
    release_hold,
    rows,
    timeline,
    what_if_drop,
)

UI = ROOT.parent / "portals/tools/university-explorer"


def serve(world=DEFAULT_OUTPUT, port=4310, database_url=None):
    world = Path(world).resolve()
    if not database_url and not (world / "university.sqlite").exists():
        raise SystemExit("Build the world first: python3 tools/university/build.py")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def json(self, data, status=200):
            raw = json.dumps(data, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def database(self, writable=False):
            if database_url:
                if writable or self.headers.get("X-University-Sandbox"):
                    raise Rejected(
                        "Sandbox operations are unavailable in PostgreSQL live mode"
                    )
                from live import LiveDatabase

                return LiveDatabase(database_url)

            sandbox = self.headers.get("X-University-Sandbox")
            if sandbox:
                if len(sandbox) != 24 or any(
                    c not in "0123456789abcdef" for c in sandbox
                ):
                    raise Rejected("Invalid sandbox identifier")
                path = world / "runs" / sandbox / "university.sqlite"
                if not path.exists():
                    raise Rejected("Unknown sandbox")
            else:
                if writable:
                    raise Rejected(
                        "Create an isolated sandbox before performing actions"
                    )
                path = world / "university.sqlite"
            return connect(path, writable)

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            q = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            if not path.startswith("/api/"):
                file = {
                    "/": "index.html",
                    "/app.js": "app.js",
                    "/style.css": "style.css",
                }.get(path)
                if not file:
                    return self.json({"error": "Not found"}, 404)
                target = UI / file
                if not target.exists():
                    return self.json({"error": "Explorer assets missing"}, 503)
                raw = target.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type", mimetypes.guess_type(file)[0] + "; charset=utf-8"
                )
                self.send_header("Content-Length", str(len(raw)))
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(raw)
                return
            try:
                if path == "/api/mode":
                    return self.json(
                        {
                            "mode": "live" if database_url else "evaluation",
                            "source": "PostgreSQL"
                            if database_url
                            else "SQLite evaluation fixture",
                        }
                    )
                if database_url and path in {"/api/scenarios", "/api/sandboxes"}:
                    return self.json(
                        {
                            "error": "Evaluation-only capability; use a separate evaluation server"
                        },
                        403,
                    )
                if database_url and path in {
                    "/api/documents",
                    "/api/relationships",
                    "/api/work-item",
                    "/api/financial-plan",
                    "/api/work-board",
                    "/api/campus-life",
                    "/api/student-profile",
                    "/api/staff-profile",
                }:
                    from live import projection

                    return self.json(
                        asyncio.run(
                            projection(
                                database_url,
                                path.removeprefix("/api/"),
                                q.get(
                                    "student_id", "ac2fa509-b4e3-402d-900b-ffb8440fc430"
                                ),
                                q.get(
                                    "actor_id", "01973261-954a-5019-8e9e-24a699abea7b"
                                ),
                                int(q.get("offset", "0")),
                                q.get("work_item_id"),
                            )
                        )
                    )
                with self.database() as db:
                    if path == "/api/overview":
                        data = dict(
                            clock=CLOCK,
                            version="Aster v3",
                            counts={
                                t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                                for t in [
                                    "student",
                                    "staff",
                                    "office",
                                    "policy",
                                    "course",
                                    "enrollment",
                                    "workflow",
                                    "event",
                                    "bed",
                                ]
                            },
                            programs=rows(
                                db,
                                "SELECT p.*,count(s.id) AS students FROM program p LEFT JOIN student s ON s.program_id=p.id GROUP BY p.id ORDER BY students DESC",
                            ),
                            issues=rows(
                                db, "SELECT * FROM source_issue ORDER BY intentional,id"
                            ),
                            deadlines=rows(
                                db,
                                "SELECT * FROM calendar WHERE starts_at>=? ORDER BY starts_at LIMIT 8",
                                (CLOCK,),
                            ),
                            health=dict(
                                active_holds=db.execute(
                                    "SELECT count(*) FROM hold WHERE released_at IS NULL"
                                ).fetchone()[0],
                                pending_payments=db.execute(
                                    "SELECT count(*) FROM payment WHERE status='pending'"
                                ).fetchone()[0],
                                waiting_cases=db.execute(
                                    "SELECT count(*) FROM workflow WHERE status<>'resolved'"
                                ).fetchone()[0],
                                occupied_beds=db.execute(
                                    "SELECT count(*) FROM housing WHERE status='assigned' AND ends_at IS NULL"
                                ).fetchone()[0],
                            ),
                            manifest=(
                                {
                                    "mode": "live",
                                    "source": "PostgreSQL canonical runtime",
                                    "validation_errors": [],
                                    "seed": "See import provenance",
                                    "sources": [],
                                    "source_hashes": {},
                                    "counts": {},
                                    "scenarios": 0,
                                    "version": "PostgreSQL live",
                                    "clock": CLOCK,
                                }
                                if database_url
                                else json.loads((world / "manifest.json").read_text())
                            ),
                        )
                    elif path == "/api/students":
                        clauses = ["1=1"]
                        args = []
                        if q.get("q"):
                            clauses.append(
                                "(s.name LIKE ? OR s.external_ref LIKE ? OR s.email LIKE ?)"
                            )
                            args += ["%" + q["q"] + "%"] * 3
                        for field in ["program_id", "residency", "status"]:
                            if q.get(field):
                                clauses.append(f"s.{field}=?")
                                args.append(q[field])
                        if q.get("held") == "true":
                            clauses.append(
                                "EXISTS(SELECT 1 FROM hold h WHERE h.student_id=s.id AND released_at IS NULL)"
                            )
                        where = " AND ".join(clauses)
                        limit = min(max(int(q.get("limit", "60")), 1), 200)
                        offset = max(int(q.get("offset", "0")), 0)
                        data = dict(
                            total=db.execute(
                                "SELECT count(*) FROM student s WHERE " + where, args
                            ).fetchone()[0],
                            items=rows(
                                db,
                                "SELECT s.*,p.name AS program_name,COALESCE(l.credits,0) AS credits,COALESCE(b.balance_cents,0) AS balance_cents,(SELECT count(*) FROM hold h WHERE h.student_id=s.id AND h.released_at IS NULL) AS holds FROM student s JOIN program p ON p.id=s.program_id LEFT JOIN current_load l ON l.student_id=s.id AND l.term_id='2026FA' LEFT JOIN account_balance b ON b.student_id=s.id AND b.term_id='2026FA' WHERE "
                                + where
                                + " ORDER BY external_ref LIMIT ? OFFSET ?",
                                args + [limit, offset],
                            ),
                        )
                    elif path.startswith("/api/students/"):
                        sid = path.split("/")[3]
                        data = evidence(db, sid, q.get("role", "staff"))
                        if database_url:
                            from live import projection

                            canonical = asyncio.run(
                                projection(
                                    database_url,
                                    "student-overview",
                                    sid,
                                    q.get(
                                        "actor_id",
                                        "01973261-954a-5019-8e9e-24a699abea7b",
                                    ),
                                )
                            )
                            data["student"] = canonical["student"]
                        data["timeline"] = timeline(
                            db,
                            sid,
                            q.get("known_at", CLOCK),
                            q.get("effective_at", CLOCK),
                            q.get("role", "staff"),
                        )
                    elif path == "/api/evidence":
                        data = evidence(
                            db, q.get("student_id", ""), q.get("role", "student")
                        )
                    elif path == "/api/timeline":
                        data = timeline(
                            db,
                            q.get("student_id", ""),
                            q.get("known_at", CLOCK),
                            q.get("effective_at", CLOCK),
                            q.get("role", "student"),
                        )
                    elif path == "/api/policies":
                        data = policies(
                            db,
                            q.get("q", ""),
                            q.get("role", "staff"),
                            q.get("at", CLOCK),
                            q.get("known_at", CLOCK),
                            q.get("student_id"),
                            q.get("history") == "true",
                        )
                    elif path == "/api/academics":
                        data = dict(
                            courses=rows(db, "SELECT * FROM course ORDER BY code"),
                            requirements=rows(
                                db,
                                "SELECT r.*,c.code FROM requirement r LEFT JOIN course c ON c.id=r.course_id ORDER BY program_id,recommended_term,code",
                            ),
                            prerequisites=rows(
                                db,
                                "SELECT p.*,c.code AS required_code FROM prerequisite p JOIN course c ON c.id=p.required_course_id",
                            ),
                        )
                    elif path == "/api/staff":
                        data = dict(
                            staff=rows(
                                db,
                                "SELECT s.*,o.name AS office_name,(SELECT count(*) FROM assignment a WHERE a.staff_id=s.id AND a.ends_at IS NULL) AS caseload FROM staff s JOIN office o ON o.id=s.office_id ORDER BY o.name,s.name",
                            ),
                            offices=rows(db, "SELECT * FROM office ORDER BY name"),
                            absences=rows(
                                db,
                                "SELECT a.*,s.name AS name,c.name AS covering_name FROM staff_absence a JOIN staff s ON s.id=a.staff_id JOIN staff c ON c.id=a.covering_staff_id",
                            ),
                        )
                    elif path == "/api/workflows":
                        data = dict(
                            cases=rows(
                                db,
                                "SELECT w.*,s.name AS student_name,s.external_ref,o.name AS owner_name FROM workflow w JOIN student s ON s.id=w.student_id JOIN staff o ON o.id=w.owner_id ORDER BY due_at",
                            ),
                            steps=rows(db, "SELECT * FROM workflow_step"),
                            dependencies=rows(db, "SELECT * FROM step_dependency"),
                        )
                    elif path == "/api/calendar":
                        data = rows(
                            db,
                            "SELECT c.*,o.name AS office_name FROM calendar c JOIN office o ON o.id=c.office_id ORDER BY starts_at",
                        )
                    elif path == "/api/housing":
                        data = dict(
                            residences=rows(
                                db,
                                "SELECT r.*,(SELECT count(*) FROM housing h JOIN bed b ON b.id=h.bed_id WHERE b.residence_id=r.id AND h.status='assigned' AND h.ends_at IS NULL) AS occupied,(SELECT count(*) FROM bed b WHERE b.residence_id=r.id AND b.accessible=1) AS accessible_beds FROM residence r",
                            ),
                            waitlist=rows(
                                db,
                                "SELECT h.*,s.name,s.external_ref FROM housing h JOIN student s ON s.id=h.student_id WHERE h.status='waitlisted'",
                            ),
                        )
                    elif path == "/api/cohort":
                        data = cohort(db)
                    elif path == "/api/schema":
                        data = rows(
                            db,
                            "SELECT name,type,sql FROM sqlite_master WHERE type IN ('table','view') ORDER BY name",
                        )
                    elif path == "/api/scenarios":
                        # Operator route. Evaluation harnesses use /api/evidence, never this route.
                        data = json.loads((world / "oracle.json").read_text())
                        if q.get("rubric") != "true":
                            data = [
                                {
                                    k: v
                                    for k, v in s.items()
                                    if k not in ("expected", "forbidden")
                                }
                                for s in data
                            ]
                    else:
                        return self.json({"error": "Not found"}, 404)
                self.json(data)
            except (Rejected, ValueError) as e:
                self.json({"error": str(e)}, 400)
            except Exception:
                self.json(
                    {
                        "error": "World query failed; inspect the local server and database"
                    },
                    500,
                )
                raise

        def do_POST(self):
            if database_url:
                return self.json(
                    {
                        "error": "Atlas live mode is read-only; use canonical product commands"
                    },
                    403,
                )
            # Local browser mutations must originate from this explorer. This is not an authentication service.
            origin = self.headers.get("Origin")
            if origin and origin not in (
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            ):
                return self.json({"error": "Origin rejected"}, 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.json({"error": "JSON required"}, 415)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 16384:
                    raise Rejected("Request too large")
                payload = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(payload, dict):
                    raise Rejected("Expected JSON object")
                path = urlsplit(self.path).path
                if path == "/api/sandboxes":
                    token = secrets.token_hex(12)
                    folder = world / "runs" / token
                    folder.mkdir(parents=True)
                    with (
                        connect(world / "university.sqlite") as source,
                        sqlite3.connect(folder / "university.sqlite") as dest,
                    ):
                        source.backup(dest)
                    return self.json(
                        dict(
                            sandbox_id=token,
                            clock=CLOCK,
                            note="Fresh baseline copy; actions affect this sandbox only.",
                        ),
                        201,
                    )
                if path == "/api/what-if/drop":
                    with self.database() as db:
                        data = what_if_drop(
                            db,
                            payload.get("student_id"),
                            payload.get("enrollment_id"),
                            payload.get("at", CLOCK),
                        )
                elif path == "/api/actions/release-hold":
                    with self.database(True) as db:
                        data = release_hold(db, payload)
                else:
                    return self.json({"error": "Not found"}, 404)
                self.json(data)
            except (Rejected, ValueError, TypeError) as e:
                self.json({"error": str(e)}, 409)

    print(f"Aster University Explorer: http://127.0.0.1:{port} — {world}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--world", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--port", type=int, default=4310)
    p.add_argument(
        "--database-url",
        help="Explicit loopback PostgreSQL DB; disables all sandbox/oracle routes",
    )
    a = p.parse_args()
    serve(a.world, a.port, a.database_url)
