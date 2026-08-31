SET PAGESIZE 50000
SET LINESIZE 400
SET TRIMSPOOL ON
SET WRAP OFF

PROMPT === INDEXES ===
SELECT i.table_name,
       i.index_name,
       i.uniqueness,
       c.column_name,
       c.column_position,
       c.descend
FROM user_indexes i
JOIN user_ind_columns c ON i.index_name = c.index_name
ORDER BY i.table_name, i.index_name, c.column_position;

PROMPT === SEQUENCES ===
SELECT sequence_name, min_value, max_value, increment_by, cycle_flag, cache_size, last_number
FROM user_sequences
ORDER BY sequence_name;

PROMPT === IDENTITY COLUMNS ===
SELECT table_name, column_name, generation_type, identity_options
FROM user_tab_identity_cols
ORDER BY table_name, column_name;

PROMPT === TRIGGERS ===
SELECT trigger_name, table_name, triggering_event, status
FROM user_triggers
ORDER BY table_name, trigger_name;

PROMPT === TABLE COMMENTS ===
SELECT table_name, comments FROM user_tab_comments WHERE comments IS NOT NULL ORDER BY table_name;

PROMPT === COLUMN COMMENTS ===
SELECT table_name, column_name, comments FROM user_col_comments WHERE comments IS NOT NULL ORDER BY table_name, column_id;

EXIT
