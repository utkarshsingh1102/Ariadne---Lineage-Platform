-- Recursive CTE that builds an org-chart from a manager hierarchy.
WITH RECURSIVE org_tree(employee_id, manager_id, depth) AS (
    SELECT employee_id, manager_id, 0 AS depth
    FROM prod.hr.employees
    WHERE manager_id IS NULL
    UNION ALL
    SELECT e.employee_id, e.manager_id, t.depth + 1
    FROM prod.hr.employees e
    JOIN org_tree t ON e.manager_id = t.employee_id
)
INSERT INTO prod.mart.org_tree
SELECT employee_id, manager_id, depth FROM org_tree;
