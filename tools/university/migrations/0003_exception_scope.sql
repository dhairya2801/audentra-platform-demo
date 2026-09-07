ALTER TABLE exception ADD COLUMN term_id TEXT REFERENCES term;
ALTER TABLE exception ADD COLUMN minimum_credits INTEGER CHECK(minimum_credits>=0);
ALTER TABLE exception ADD COLUMN maximum_credits INTEGER CHECK(maximum_credits>0);
