CREATE TABLE production_tasks (
	id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	title TEXT NOT NULL CHECK (length(trim(title)) > 0),
	status TEXT NOT NULL DEFAULT 'pending'
		CHECK (status IN ('pending', 'in_progress', 'completed')),
	creatred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
	updated_at TIMESTAMPTZ NOT NULL DEFAULT  CURRENT_TIMESTAMP
);
