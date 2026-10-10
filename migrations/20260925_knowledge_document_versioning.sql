-- 仅为 knowledge 知识库增加通用文档版本管理；不修改 breakdown 故障案例库。
ALTER TABLE document_knowledge
  ADD COLUMN document_code VARCHAR(128) NULL,
  ADD COLUMN document_type VARCHAR(64) NOT NULL DEFAULT 'other',
  ADD COLUMN category_path VARCHAR(512) NULL,
  ADD COLUMN lifecycle_status VARCHAR(32) NOT NULL DEFAULT 'active',
  ADD COLUMN applicability_mode VARCHAR(32) NOT NULL DEFAULT 'global',
  ADD COLUMN default_version_id INT NULL,
  ADD COLUMN updated_time DATETIME NULL,
  ADD UNIQUE KEY uk_document_knowledge_code (document_code),
  ADD KEY ix_document_knowledge_type (document_type),
  ADD KEY ix_document_knowledge_category (category_path),
  ADD KEY ix_document_knowledge_lifecycle (lifecycle_status),
  ADD KEY ix_document_knowledge_default_version (default_version_id);

CREATE TABLE knowledge_document_types (
  id INT PRIMARY KEY AUTO_INCREMENT,
  type_code VARCHAR(64) NOT NULL UNIQUE,
  type_name VARCHAR(128) NOT NULL,
  description TEXT NULL,
  requires_review BOOLEAN NOT NULL DEFAULT TRUE,
  requires_applicability BOOLEAN NOT NULL DEFAULT FALSE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_time DATETIME NOT NULL,
  updated_time DATETIME NOT NULL
);

CREATE TABLE knowledge_document_versions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  document_id INT NOT NULL,
  version_major INT NOT NULL,
  version_minor INT NOT NULL,
  version_label VARCHAR(32) NOT NULL,
  draft_number INT NULL,
  based_on_version_id INT NULL,
  workflow_status VARCHAR(32) NOT NULL DEFAULT 'draft',
  validity_status VARCHAR(32) NOT NULL DEFAULT 'inactive',
  change_category VARCHAR(32) NOT NULL DEFAULT 'minor',
  change_summary TEXT NOT NULL,
  change_reason TEXT NULL,
  effective_from DATETIME NULL,
  effective_to DATETIME NULL,
  title_snapshot VARCHAR(255) NOT NULL,
  tag_snapshot JSON NULL,
  image_urls_snapshot MEDIUMTEXT NULL,
  origin_file_name VARCHAR(255) NULL,
  origin_file_dir TEXT NULL,
  content_hash CHAR(64) NOT NULL,
  parser_version VARCHAR(64) NULL,
  embedding_model_version VARCHAR(128) NULL,
  index_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  index_error TEXT NULL,
  indexed_at DATETIME NULL,
  revision INT NOT NULL DEFAULT 1,
  created_by INT NOT NULL,
  reviewed_by INT NULL,
  published_by INT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  reviewed_at DATETIME NULL,
  published_at DATETIME NULL,
  UNIQUE KEY uk_knowledge_document_version (document_id, version_major, version_minor),
  KEY ix_kdv_document_status (document_id, workflow_status, validity_status),
  CONSTRAINT fk_kdv_document FOREIGN KEY (document_id) REFERENCES document_knowledge(id),
  CONSTRAINT fk_kdv_base FOREIGN KEY (based_on_version_id) REFERENCES knowledge_document_versions(id),
  CONSTRAINT fk_kdv_created_by FOREIGN KEY (created_by) REFERENCES users(id),
  CONSTRAINT fk_kdv_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES users(id),
  CONSTRAINT fk_kdv_published_by FOREIGN KEY (published_by) REFERENCES users(id)
);

CREATE TABLE knowledge_document_version_sections (
  id INT PRIMARY KEY AUTO_INCREMENT,
  version_id INT NOT NULL,
  stable_section_key VARCHAR(255) NOT NULL,
  parent_section_key VARCHAR(255) NULL,
  section_index INT NOT NULL DEFAULT 0,
  section_title VARCHAR(255) NULL,
  section_type VARCHAR(64) NULL,
  plain_text MEDIUMTEXT NULL,
  image_urls JSON NULL,
  metadata JSON NULL,
  content_hash CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL,
  UNIQUE KEY uk_kdvs_version_section_key (version_id, stable_section_key),
  CONSTRAINT fk_kdvs_version FOREIGN KEY (version_id) REFERENCES knowledge_document_versions(id)
);

CREATE TABLE knowledge_document_version_files (
  id INT PRIMARY KEY AUTO_INCREMENT,
  version_id INT NOT NULL,
  file_role VARCHAR(32) NOT NULL DEFAULT 'attachment',
  original_name VARCHAR(255) NOT NULL,
  storage_path TEXT NOT NULL,
  mime_type VARCHAR(128) NULL,
  file_size INT NULL,
  content_hash CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL,
  KEY ix_kdvf_version (version_id),
  CONSTRAINT fk_kdvf_version FOREIGN KEY (version_id) REFERENCES knowledge_document_versions(id)
);

CREATE TABLE machine_models (
  id INT PRIMARY KEY AUTO_INCREMENT,
  model_code VARCHAR(128) NOT NULL UNIQUE,
  model_name VARCHAR(255) NOT NULL,
  lifecycle_status VARCHAR(32) NOT NULL DEFAULT 'active',
  production_start DATETIME NULL, production_end DATETIME NULL, service_end DATETIME NULL
);
CREATE TABLE machine_model_aliases (
  id INT PRIMARY KEY AUTO_INCREMENT,
  machine_model_id INT NOT NULL,
  alias_name VARCHAR(255) NOT NULL,
  UNIQUE KEY uk_machine_model_alias (machine_model_id, alias_name),
  CONSTRAINT fk_mma_model FOREIGN KEY (machine_model_id) REFERENCES machine_models(id)
);
CREATE TABLE machine_revisions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  machine_model_id INT NOT NULL,
  revision_code VARCHAR(128) NOT NULL,
  hardware_version VARCHAR(128) NULL,
  software_version_min VARCHAR(128) NULL, software_version_max VARCHAR(128) NULL,
  serial_number_from VARCHAR(128) NULL, serial_number_to VARCHAR(128) NULL,
  production_start DATETIME NULL, production_end DATETIME NULL,
  KEY ix_machine_revision_model (machine_model_id),
  CONSTRAINT fk_mr_model FOREIGN KEY (machine_model_id) REFERENCES machine_models(id)
);
CREATE TABLE knowledge_version_applicability (
  id INT PRIMARY KEY AUTO_INCREMENT,
  version_id INT NOT NULL,
  machine_model_id INT NULL, machine_revision_id INT NULL,
  serial_number_from VARCHAR(128) NULL, serial_number_to VARCHAR(128) NULL,
  firmware_min VARCHAR(128) NULL, firmware_max VARCHAR(128) NULL,
  region_code VARCHAR(64) NULL, site_id INT NULL,
  effective_from DATETIME NULL, effective_to DATETIME NULL,
  priority INT NOT NULL DEFAULT 100,
  applicability_status VARCHAR(32) NOT NULL DEFAULT 'active',
  exclusion_reason TEXT NULL,
  KEY ix_kva_version_status (version_id, applicability_status),
  CONSTRAINT fk_kva_version FOREIGN KEY (version_id) REFERENCES knowledge_document_versions(id),
  CONSTRAINT fk_kva_model FOREIGN KEY (machine_model_id) REFERENCES machine_models(id),
  CONSTRAINT fk_kva_revision FOREIGN KEY (machine_revision_id) REFERENCES machine_revisions(id)
);
CREATE TABLE knowledge_version_relations (
  id INT PRIMARY KEY AUTO_INCREMENT,
  source_version_id INT NOT NULL, target_version_id INT NOT NULL,
  relation_type VARCHAR(64) NOT NULL, reason TEXT NULL,
  created_by INT NOT NULL, created_at DATETIME NOT NULL,
  UNIQUE KEY uk_kvr_relation (source_version_id, target_version_id, relation_type),
  CONSTRAINT fk_kvr_source FOREIGN KEY (source_version_id) REFERENCES knowledge_document_versions(id),
  CONSTRAINT fk_kvr_target FOREIGN KEY (target_version_id) REFERENCES knowledge_document_versions(id),
  CONSTRAINT fk_kvr_user FOREIGN KEY (created_by) REFERENCES users(id)
);
CREATE TABLE knowledge_version_audit_logs (
  id INT PRIMARY KEY AUTO_INCREMENT,
  document_id INT NOT NULL, version_id INT NULL,
  action VARCHAR(64) NOT NULL, operator_id INT NOT NULL,
  payload_json JSON NULL, created_at DATETIME NOT NULL,
  KEY ix_kval_document (document_id), KEY ix_kval_version (version_id),
  CONSTRAINT fk_kval_document FOREIGN KEY (document_id) REFERENCES document_knowledge(id),
  CONSTRAINT fk_kval_version FOREIGN KEY (version_id) REFERENCES knowledge_document_versions(id),
  CONSTRAINT fk_kval_user FOREIGN KEY (operator_id) REFERENCES users(id)
);

INSERT INTO knowledge_document_types
  (type_code, type_name, description, requires_review, requires_applicability, is_active, created_time, updated_time)
VALUES
  ('operation_guide','操作指导','标准操作和现场作业指导',TRUE,TRUE,TRUE,NOW(),NOW()),
  ('maintenance_guide','维修指导','设备维护、保养和维修知识',TRUE,TRUE,TRUE,NOW(),NOW()),
  ('service_manual','服务手册','完整设备或产品服务手册',TRUE,TRUE,TRUE,NOW(),NOW()),
  ('technical_notice','技术公告','技术变更、风险和临时通知',TRUE,FALSE,TRUE,NOW(),NOW()),
  ('troubleshooting','故障排查','通用排障流程；不替代 breakdown 案例库',TRUE,TRUE,TRUE,NOW(),NOW()),
  ('safety_instruction','安全说明','安全规范和强制要求',TRUE,FALSE,TRUE,NOW(),NOW()),
  ('training_material','培训材料','培训课件及辅助材料',FALSE,FALSE,TRUE,NOW(),NOW()),
  ('faq','常见问题','常见问题知识',FALSE,FALSE,TRUE,NOW(),NOW()),
  ('other','其他','暂未分类的知识文档',TRUE,FALSE,TRUE,NOW(),NOW());

UPDATE document_knowledge SET document_code = CONCAT('KB-', LPAD(id, 6, '0')) WHERE document_code IS NULL;
