ALTER TABLE campus_event
  ADD COLUMN visual_theme varchar(32)
    CHECK (visual_theme IN ('festival', 'discovery', 'career', 'community')),
  ADD COLUMN image_url varchar(1000),
  ADD COLUMN image_alt varchar(500),
  ADD COLUMN image_attribution varchar(500),
  ADD COLUMN image_source_url varchar(1000);
