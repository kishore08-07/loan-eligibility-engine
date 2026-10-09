-- "Process anyway": a re-upload of a file that was detected as a DUPLICATE skips the duplicate check
-- (and is stored without a file_hash, so the unique index on finished hashes is not violated).
ALTER TABLE upload_jobs ADD COLUMN allow_duplicate BOOLEAN NOT NULL DEFAULT false;
