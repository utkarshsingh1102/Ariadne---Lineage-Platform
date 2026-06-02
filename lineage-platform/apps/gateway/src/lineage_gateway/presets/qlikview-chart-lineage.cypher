// QlikView chart → field → derived chain → physical :Table.
MATCH path = (c:QlikChart)-[:USES_FIELD]->(:Attribute)
             -[:DERIVES_FROM*0..6]->(col:Attribute)
             <-[:HAS_COLUMN]-(t:Table)
RETURN path
LIMIT 200
