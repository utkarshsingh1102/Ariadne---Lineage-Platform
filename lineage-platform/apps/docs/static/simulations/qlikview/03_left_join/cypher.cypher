// QlikView app -> Cypher sketch
MERGE (s:QlikScript {id: $id})
  SET s.name = $name, s.file_path = $file_path

// One MERGE per LOAD statement:
MERGE (t:QlikTable {id: $id})
  SET t.name = $name, t.source_type = $source_type
MERGE (s)-[:CONTAINS_TABLE]->(t)

// Plus attribute MERGEs per field; see qlikview-parser/graph/writer.py.