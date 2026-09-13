-- The deterministic world's academic-progress view was omitted from the original
-- PostgreSQL schema port. Preserve its attempt/earned-credit/GPA semantics with
-- tenant-bound joins and invoker permissions over canonical history.
CREATE VIEW university.academic_progress WITH (security_invoker=true) AS
 SELECT e.tenant_id,e.student_id,SUM(c.credits) AS attempted_credits,
   SUM(CASE WHEN e.grade IN ('A','B','C','D','P') THEN c.credits ELSE 0 END) AS earned_credits,
   ROUND(SUM(CASE e.grade WHEN 'A' THEN c.credits*4 WHEN 'B' THEN c.credits*3
     WHEN 'C' THEN c.credits*2 WHEN 'D' THEN c.credits ELSE 0 END)::numeric /
     NULLIF(SUM(CASE WHEN e.grade IN ('A','B','C','D','F') THEN c.credits ELSE 0 END),0),3) AS gpa
 FROM university.enrollment e
 JOIN university.section s ON s.tenant_id=e.tenant_id AND s.id=e.section_id
 JOIN university.course c ON c.tenant_id=s.tenant_id AND c.id=s.course_id
 WHERE e.status IN ('completed','withdrawn') GROUP BY e.tenant_id,e.student_id;
