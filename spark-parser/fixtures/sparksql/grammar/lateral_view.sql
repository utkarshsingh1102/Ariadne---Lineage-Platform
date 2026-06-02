-- LATERAL VIEW explode + LATERAL VIEW OUTER posexplode.
CREATE TABLE prod.mart.user_tags AS
SELECT
    u.user_id,
    exploded_tag,
    tag_pos
FROM prod.dim.users u
LATERAL VIEW explode(u.tags) tbl AS exploded_tag
LATERAL VIEW OUTER posexplode(u.scores) scr AS tag_pos, score_value;
