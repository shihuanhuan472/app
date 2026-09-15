ALTER TABLE `fault_case_memory`
  ADD COLUMN IF NOT EXISTS `scope` VARCHAR(255) NULL AFTER `case_key`;

CREATE INDEX IF NOT EXISTS `idx_fault_case_memory_scope` ON `fault_case_memory` (`scope`);
