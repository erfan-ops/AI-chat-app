-- Schema inspection script for the AI chat application database.
-- Run: sqlplus -S chatbot/chatbot@192.168.1.42:1521/pdb.oracle.ek @inspect_schema.sql
SET PAGESIZE 50000
SET LINESIZE 400
SET TRIMSPOOL ON
SET LONG 4000
SET WRAP OFF

PROMPT ============================================
PROMPT 1. TABLES OWNED BY CHATBOT
PROMPT ============================================
SELECT table_name FROM user_tables ORDER BY table_name;

PROMPT ============================================
PROMPT 2. COLUMNS (all tables)
PROMPT ============================================
SELECT table_name,
       column_id,
       column_name,
       data_type,
       data_length,
       data_precision,
       data_scale,
       nullable,
       data_default
FROM user_tab_columns
ORDER BY table_name, column_id;

PROMPT ============================================
PROMPT 3. CONSTRAINTS (incl. columns + referenced constraint)
PROMPT ============================================
SELECT c.table_name,
       c.constraint_name,
       c.constraint_type,
       c.search_condition,
       c.r_constraint_name,
       cc.column_name,
       cc.position
FROM user_constraints c
LEFT JOIN user_cons_columns cc ON c.constraint_name = cc.constraint_name
ORDER BY c.table_name, c.constraint_name, cc.position;

PROMPT ============================================
PROMPT 4. INDEXES
PROMPT ============================================
SELECT i.table_name,
       i.index_name,
       i.uniqueness,
       c.column_name,
       c.column_position,
       c.descend
FROM user_indexes i
JOIN user_ind_columns c ON i.index_name = c.index_name
ORDER BY i.table_name, i.index_name, c.column_position;

PROMPT ============================================
PROMPT 5. SEQUENCES
PROMPT ============================================
SELECT sequence_name, min_value, max_value, increment_by, cycle_flag, cache_size, last_number
FROM user_sequences
ORDER BY sequence_name;

PROMPT ============================================
PROMPT 6. IDENTITY COLUMNS
PROMPT ============================================
SELECT table_name, column_name, generation_type, identity_options
FROM user_tab_identity_cols
ORDER BY table_name, column_name;

PROMPT ============================================
PROMPT 7. TABLE COMMENTS
PROMPT ============================================
SELECT table_name, comments FROM user_tab_comments WHERE comments IS NOT NULL ORDER BY table_name;

PROMPT ============================================
PROMPT 8. COLUMN COMMENTS
PROMPT ============================================
SELECT table_name, column_name, comments FROM user_col_comments WHERE comments IS NOT NULL ORDER BY table_name, column_id;

PROMPT ============================================
PROMPT 9. TRIGGERS
PROMPT ============================================
SELECT trigger_name, table_name, triggering_event, status
FROM user_triggers
ORDER BY table_name, trigger_name;

EXIT
