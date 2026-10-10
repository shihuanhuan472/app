ALTER TABLE knowledge_document_versions
    ADD COLUMN planned_publish_mode VARCHAR(32) NOT NULL DEFAULT 'replace' AFTER hardware_version,
    ADD COLUMN planned_supersedes_version_ids JSON NULL AFTER planned_publish_mode;
