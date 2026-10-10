CREATE TABLE knowledge_categories (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  parent_id INT NULL,
  full_path VARCHAR(512) NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_time DATETIME NOT NULL,
  updated_time DATETIME NOT NULL,
  UNIQUE KEY uk_knowledge_category_parent_name (parent_id, name),
  UNIQUE KEY uk_knowledge_category_full_path (full_path),
  KEY ix_knowledge_category_parent_sort (parent_id, sort_order),
  CONSTRAINT fk_knowledge_category_parent FOREIGN KEY (parent_id) REFERENCES knowledge_categories(id)
);

ALTER TABLE document_knowledge
  ADD COLUMN category_id INT NULL AFTER document_type,
  ADD KEY ix_document_knowledge_category_id (category_id),
  ADD CONSTRAINT fk_document_knowledge_category FOREIGN KEY (category_id) REFERENCES knowledge_categories(id);

-- category_path 保留为路径快照，兼容历史数据、检索和导出。
