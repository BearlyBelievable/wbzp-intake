-- migrate:up
CREATE TABLE IF NOT EXISTS pending_applications (
    email TEXT PRIMARY KEY,
    submitted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submission_attempts (
    ip TEXT NOT NULL,
    email TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submission_attempts_ip ON submission_attempts (ip);
CREATE INDEX IF NOT EXISTS idx_submission_attempts_email ON submission_attempts (email);
CREATE INDEX IF NOT EXISTS idx_submission_attempts_attempted_at ON submission_attempts (attempted_at);

CREATE TABLE IF NOT EXISTS held_applications (
    email TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    failed_at TEXT NOT NULL
);
