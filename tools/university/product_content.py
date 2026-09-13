"""Deterministic approved campus publication input; PostgreSQL owns live edits."""

import json
from pathlib import Path
from uuid import UUID, uuid5

DIRECTORY = Path(__file__).with_name("fixtures") / "club-directory.json"


def publications():
    return json.loads(DIRECTORY.read_text())["clubs"]


async def import_clubs(raw, tenant):
    for club in publications():
        await raw.execute(
            """INSERT INTO public.student_club
            (id,tenant_id,name,category,description,long_description,meeting_schedule,
             contact_name,contact_role,contact_channel,latest_update,next_activity,
             source_label,source_status,social_links,membership_open,active,version,
             created_at,updated_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,'Office of Student Life','Office contact',
             'studentlife@synthetic.aster.example','Published 2026–2027 club directory','',
             'Office of Student Life','tenant_authored','[]',true,true,1,
             '2026-09-08T16:00:00Z','2026-09-08T16:00:00Z')
            ON CONFLICT(tenant_id,name) DO NOTHING""",
            uuid5(UUID(str(tenant)), "club-directory:" + club["name"]),
            UUID(str(tenant)),
            club["name"],
            club["category"],
            club["description"],
            club["longDescription"],
            club["meetingSchedule"],
        )
