"""Fill the local Camila board with stored demo evidence; preserve subsequent user activity."""
import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

import fitz
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from audentra.core.auth import AuthContext
from audentra.infrastructure.postgres.demo_task_board_commands import DemoTaskBoardCommands
from audentra.infrastructure.postgres.demo_task_board_repository import DemoTaskBoardProjection
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.storage import create_object_storage
from run_runtime import runtime_settings

TENANT = "00000000-0000-7000-8000-000000000003"
STAFF = "01973261-954a-5019-8e9e-24a699abea7b"
VERSION = "camila-connected-v1"


def identity(value):
    return str(uuid5(NAMESPACE_URL, f"audentra:{VERSION}:{value}"))


def sample_pdf(card):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 65), "STUDENT RECORDS | DEMO UNIVERSITY", fontsize=18)
        page.insert_text((50, 115), "Supporting document", fontsize=24)
        lines = [card["title"], "", "Student: " + card["student"]["name"],
                 "Student reference: " + card["student"]["externalRef"],
                 "Program: " + card["student"]["program"],
                 "Assigned adviser: Camila Abernathy", "",
                 "This stored document belongs to the demonstration student record.",
                 "It is sample evidence for reviewing the original, requesting changes,",
                 "and submitting a replacement through the student portal.", "",
                 "Document extraction and policy checks are simulated."]
        page.insert_textbox(fitz.Rect(50, 150, 540, 650), "\n".join(lines), fontsize=12)
        page.insert_text((50, 755), "DEMONSTRATION DOCUMENT - NOT AN OFFICIAL CREDENTIAL", fontsize=10)
        return pdf.tobytes(no_new_id=True)


async def prepare_requirements(engine, cards, templates):
    """One transaction clones definitions without changing historical requirement evidence."""
    async with engine.begin() as c:
        await c.execute(text("SELECT set_config('audentra.tenant_id',:t,true)"), {"t":TENANT})
        for student in {card["student"]["id"] for card in cards}:
            params = {"tenant":TENANT,"student":student,"definition":identity("journey:"+student)}
            journey = (await c.execute(text("""
                SELECT * FROM enrollment_journey WHERE tenant_id=CAST(:tenant AS uuid)
                  AND student_id=CAST(:student AS uuid) AND status IN ('in_progress','completed')
                ORDER BY created_at DESC LIMIT 1 FOR UPDATE
            """),params)).mappings().first()
            if journey is None:
                raise ValueError("Every demo student needs an enrollment journey")
            params.update(journey=str(journey["id"]),original=str(journey["journey_definition_version_id"]))
            await c.execute(text("""
                INSERT INTO journey_definition_version
                SELECT (jsonb_populate_record(NULL::journey_definition_version,to_jsonb(v) ||
                  jsonb_build_object('id',CAST(:definition AS text),'code','demo_' || CAST(:student AS text),
                    'version',1,'active',0,'created_at',now(),'updated_at',now()))).*
                FROM journey_definition_version v WHERE v.id=CAST(:original AS uuid)
                  AND v.tenant_id=CAST(:tenant AS uuid) ON CONFLICT DO NOTHING
            """),params)
            await c.execute(text("""
                INSERT INTO journey_requirement_definition
                SELECT CAST(:definition AS uuid),requirement_definition_version_id
                FROM journey_requirement_definition WHERE journey_definition_version_id=CAST(:original AS uuid)
                ON CONFLICT DO NOTHING
            """),params)
            await c.execute(text("""
                UPDATE enrollment_journey SET journey_definition_version_id=CAST(:definition AS uuid),
                  version=version+1,updated_at=now() WHERE id=CAST(:journey AS uuid)
                  AND tenant_id=CAST(:tenant AS uuid) AND journey_definition_version_id<>CAST(:definition AS uuid)
            """),params)
            for card in [x for x in cards if x["student"]["id"]==student and templates[x["templateKey"]]["type"]=="document"]:
                # Existing uploads are authoritative; never replace their requirement or original.
                if card["documents"] or card["requirements"]:
                    continue
                params.update(work=card["id"],requirement=identity("requirement:"+card["id"]),
                              reqdef=identity("definition:"+card["id"]),code="camila_"+card["templateKey"].lower().replace('-','_'),
                              title=card["title"],description="Upload a complete document for Camila to review. Existing accepted evidence is preserved.")
                if card["templateKey"] == "ENR-184":
                    existing = await c.scalar(text("""
                        SELECT r.id FROM student_requirement r JOIN requirement_definition_version d
                          ON d.id=r.requirement_definition_version_id AND d.tenant_id=r.tenant_id
                        WHERE r.tenant_id=CAST(:tenant AS uuid) AND r.journey_id=CAST(:journey AS uuid)
                          AND d.code='ada_updated_transcript'
                    """),params)
                    if existing is None:
                        raise ValueError("Seed Ada's pending updated-transcript request first")
                    params["requirement"]=str(existing)
                else:
                    await c.execute(text("""
                        INSERT INTO requirement_definition_version(id,tenant_id,code,title,description,blocking,
                          display_order,version,submission_type,responsible_office,flow_kind,interaction_type,priority)
                        VALUES(CAST(:reqdef AS uuid),CAST(:tenant AS uuid),:code,:title,:description,0,110,1,
                          'document','Academic Advising Center','enrollment','upload_file',50) ON CONFLICT DO NOTHING
                    """),params)
                    await c.execute(text("""
                        INSERT INTO journey_requirement_definition VALUES(CAST(:definition AS uuid),CAST(:reqdef AS uuid))
                        ON CONFLICT DO NOTHING
                    """),params)
                    await c.execute(text("""
                        INSERT INTO student_requirement(id,tenant_id,journey_id,requirement_definition_version_id,status)
                        VALUES(CAST(:requirement AS uuid),CAST(:tenant AS uuid),CAST(:journey AS uuid),CAST(:reqdef AS uuid),'ready')
                        ON CONFLICT DO NOTHING
                    """),params)
                await c.execute(text("""
                    INSERT INTO staff_work_item_link(id,tenant_id,work_item_id,entity_type,entity_id,relationship)
                    VALUES(gen_random_uuid(),CAST(:tenant AS uuid),CAST(:work AS uuid),'requirement',
                      CAST(:requirement AS uuid),'document_request') ON CONFLICT DO NOTHING
                """),params)
        await c.execute(text("""
            INSERT INTO audit_event(id,tenant_id,actor_type,actor_id,action,resource_type,resource_id,
              authorization_basis,request_id,correlation_id,metadata)
            VALUES(gen_random_uuid(),CAST(:tenant AS uuid),'staff',CAST(:staff AS uuid),
              'demo.board_requirements_linked','staff_member',CAST(:staff AS uuid),'demo_setup',:request,:request,'{}')
        """),{"tenant":TENANT,"staff":STAFF,"request":str(uuid4())})


async def seed(url):
    parsed=urlparse(url)
    if parsed.hostname not in {"127.0.0.1","localhost"} or not parsed.path.startswith('/audentra_university'):
        raise ValueError('Choose an explicit local audentra_university database')
    engine=create_async_engine(url.replace('postgresql://','postgresql+asyncpg://'))
    portal=PostgresPortalRepository(engine)
    staff=PostgresStaffRepository(engine,portal)
    commands=DemoTaskBoardCommands(staff)
    projection=DemoTaskBoardProjection(staff)
    auth=AuthContext(TENANT,"3bedfe91-6802-4937-893b-72cb7779ecfa",STAFF,"staff")
    storage=create_object_storage(runtime_settings(url,enable_openai=False).object_storage)
    try:
        # A session lock guards the whole resumable seed, including storage operations.
        async with engine.connect() as guard:
            await guard.execute(text("SELECT pg_advisory_lock(hashtext(:key))"),{"key":VERSION})
            try:
                done=await guard.scalar(text("SELECT 1 FROM university.meta WHERE tenant_id=CAST(:tenant AS uuid) AND key=:key"),{"tenant":TENANT,"key":VERSION})
                if done:
                    return {"seeded":False,"reason":"Existing documents and conversations preserved"}
                cards=(await projection.read(auth))["cards"]
                templates={x["key"]:x for x in json.loads(Path(__file__).with_name('fixtures').joinpath('camila-task-board.json').read_text())["cards"]}
                await prepare_requirements(engine,cards,templates)
                cards=(await projection.read(auth))["cards"]
                for card in cards:
                    template=templates.get(card["templateKey"])
                    if not template:
                        continue
                    student=AuthContext(TENANT,card["student"]["id"],card["student"]["id"],"student")
                    if template["type"]=='document' and card["templateKey"]!='ENR-184' and template["status"]!='requested' and not card["documents"]:
                        content=sample_pdf(card);digest=hashlib.sha256(content).hexdigest()
                        req=card["requirements"][0]["id"]
                        doc=await portal.reserve_student_document_upload(student,{"fileName":f'{card["student"]["preferredName"]}-{card["templateKey"]}-demo.pdf',
                            "mimeType":"application/pdf","sizeBytes":len(content),"category":"other","sha256":digest},
                            identity("upload:"+card["id"]),str(uuid4()),req)
                        reference=await portal.get_student_document_content_reference(student,str(doc["id"]))
                        await storage.put(str(reference["storageKey"]),content,content_type='application/pdf',sha256=digest)
                        await portal.attach_demo_document(student,str(doc["id"]),str(uuid4()))
                    current=next(c for c in (await projection.read(auth))["cards"] if c["id"]==card["id"])
                    if template["type"]=='document' and current["documents"] and template["status"] in {'completed','correction'} and current["documents"][0]["status"]=='under_review':
                        rejected=template["status"]=='correction'
                        await staff.review_document(auth,current["documents"][0]["id"],{
                            "workItemId":current["id"],"expectedWorkItemVersion":current["version"],
                            "decision":"rejected" if rejected else "accepted","notifyStudent":True,
                            "note":"Please upload a complete replacement with all pages." if rejected else "The submitted demonstration document has been reviewed and accepted.",
                            **({"reasonCode":"incomplete"} if rejected else {})},str(uuid4()),identity('decision:'+card["id"]),demo_only=True)
                    elif template["type"]!='document' and not current["conversations"]:
                        await commands.write(auth,card["id"],{"kind":"message","expectedVersion":current["version"],
                            "body":f'Hi {card["student"]["preferredName"]}, I am following up on: {card["title"]}. Please reply here with any questions or updates so I can help with your next step.',
                            "startNewConversation":False},str(uuid4()),identity('message:'+card["id"]))
                    print('Prepared '+card["key"],flush=True)
                async with engine.begin() as c:
                    await staff._insert_outbox(c,auth=auth,request_id=str(uuid4()),event_name='demo.board_populated.v1',
                        aggregate_type='staff_member',aggregate_id=STAFF,aggregate_version=1,data={"scenario":VERSION})
                    await c.execute(text("INSERT INTO university.meta(tenant_id,key,value) VALUES(CAST(:tenant AS uuid),:key,:value)"),
                        {"tenant":TENANT,"key":VERSION,"value":datetime.now(UTC).isoformat()})
                result=await projection.read(auth)
                return {"seeded":True,"cards":result["total"],"students":result["studentCount"],
                        "documents":sum(len(c["documents"]) for c in result["cards"]),
                        "conversations":sum(len(c["conversations"]) for c in result["cards"])}
            finally:
                await guard.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"),{"key":VERSION})
                await storage.close()
        await engine.dispose()
    finally:
        await engine.dispose()



if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url',required=True)
    print(json.dumps(asyncio.run(seed(parser.parse_args().database_url)),indent=2))
