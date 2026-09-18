ALTER TABLE `tags`
  ADD COLUMN IF NOT EXISTS `match_aliases` JSON NULL AFTER `description`;

UPDATE `tags`
SET `match_aliases` = JSON_ARRAY('MGISEQ-200', 'DNBSEQ-G50')
WHERE `name` = 'G50'
  AND (`match_aliases` IS NULL OR JSON_LENGTH(`match_aliases`) = 0);

UPDATE `tags`
SET `match_aliases` = JSON_ARRAY('MGISEQ-2000', 'DNBSEQ-G400')
WHERE `name` = 'G400'
  AND (`match_aliases` IS NULL OR JSON_LENGTH(`match_aliases`) = 0);
