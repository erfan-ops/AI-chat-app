SET PAGESIZE 50000
SET LINESIZE 600
SET TRIMSPOOL ON
SET WRAP OFF

PROMPT === CONSTRAINT COLUMNS ===
SELECT c.table_name,
       c.constraint_name,
       c.constraint_type,
       c.r_constraint_name,
       cc.column_name,
       cc.position
FROM user_constraints c
LEFT JOIN user_cons_columns cc ON c.constraint_name = cc.constraint_name
ORDER BY c.table_name, c.constraint_name, cc.position;

PROMPT === CHECK CONSTRAINTS (search conditions) ===
SET LONG 10000
SET WRAP ON
SELECT constraint_name, table_name, search_condition
FROM user_constraints
WHERE constraint_type = 'C'
ORDER BY table_name, constraint_name;

PROMPT === REFERENTIAL CONSTRAINTS (FK detail) ===
SELECT c.constraint_name,
       c.table_name,
       c.r_constraint_name,
       r.table_name AS ref_table
FROM user_constraints c
LEFT JOIN user_constraints r ON c.r_constraint_name = r.constraint_name
WHERE c.constraint_type = 'R'
ORDER BY c.table_name, c.constraint_name;

EXIT
