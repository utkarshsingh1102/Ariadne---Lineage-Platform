// Workbook -> Cypher sketch (1 datasources)

MERGE (w:Workbook {id: $id})
  SET w.name = $name, w.file_path = $file_path

// One MERGE per datasource:
MERGE (ds:Datasource {id: $id})
  SET ds.name = $name, ds.kind = $kind
MERGE (w)-[:USES_DATASOURCE]->(ds)

// One MERGE per worksheet / dashboard etc, see tableau-parser/graph/queries.py