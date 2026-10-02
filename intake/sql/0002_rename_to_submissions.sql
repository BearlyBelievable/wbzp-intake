-- migrate:up
CREATE TABLE submissions (
    email TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('in_progress', 'awaiting_signup', 'failed')),
    submitted_at TEXT NOT NULL,
    body TEXT,
    destination_type TEXT,
    destination_channel_id INTEGER,
    destination_to TEXT,
    destination_bcc TEXT,
    destination_subject TEXT,
    failed_at TEXT
);

INSERT INTO submissions (email, status, submitted_at)
SELECT email, 'awaiting_signup', submitted_at
FROM pending_applications;

INSERT OR REPLACE INTO submissions (email, status, submitted_at, body, destination_type, destination_channel_id, destination_subject, failed_at)
SELECT email, 'failed', failed_at, body, 'zulip', :default_channel_id, subject, failed_at
FROM held_applications;

DROP TABLE pending_applications;
DROP TABLE held_applications;

-- migrate:down
CREATE TABLE pending_applications (
    email TEXT PRIMARY KEY,
    submitted_at TEXT NOT NULL
);

CREATE TABLE held_applications (
    email TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    failed_at TEXT NOT NULL
);

INSERT INTO pending_applications (email, submitted_at)
SELECT email, submitted_at
FROM submissions
WHERE status != 'failed';

INSERT INTO held_applications (email, subject, body, failed_at)
SELECT email, COALESCE(destination_subject, 'New submission'), body, failed_at
FROM submissions
WHERE status = 'failed';

DROP TABLE submissions;
